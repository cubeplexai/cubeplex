"""Startup repair of stranded Redis runs and recurring persisted Stop recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.config import config
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.streams.run_events import get_run_meta, is_stale_meta, mark_run_stale


class StoppedRunRecovery:
    """Retry durable Stop intents in bounded pages, including after Redis expiry."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        reconcile: Callable[[str], Awaitable[bool]],
        *,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("stop recovery batch size must be positive")
        self._session_maker = session_maker
        self._reconcile = reconcile
        self._batch_size = batch_size
        self._after: str | None = None

    async def reconcile_once(self) -> int:
        query = select(col(ConversationExecutionAdmission.id)).where(
            col(ConversationExecutionAdmission.run_id).is_not(None),
            col(ConversationExecutionAdmission.run_finished_at).is_(None),
            or_(
                col(ConversationExecutionAdmission.run_stop_requested_at).is_not(None),
                col(ConversationExecutionAdmission.revoked_at).is_not(None),
            ),
        )
        if self._after is not None:
            query = query.where(col(ConversationExecutionAdmission.id) > self._after)
        async with self._session_maker() as session:
            ids = list(
                await session.scalars(
                    query.order_by(col(ConversationExecutionAdmission.id)).limit(self._batch_size)
                )
            )
        for admission_id in ids:
            try:
                async with asyncio.timeout(5):
                    await self._reconcile(admission_id)
            except Exception:
                logger.opt(exception=True).warning("Stop recovery deferred for {}", admission_id)
            self._after = admission_id
        if len(ids) < self._batch_size:
            self._after = None
        return len(ids)

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.opt(exception=True).warning("Could not scan persisted Stop intents")
            await asyncio.sleep(5)


async def recover_stranded_runs(redis: Redis, *, prefix: str) -> int:
    """Scan Redis for stranded active-run keys and clean them up.

    Returns the number of stranded runs recovered.
    """
    pattern = f"{prefix}:conversation_active_run:*"
    prefix_len = len(f"{prefix}:conversation_active_run:")
    recovered: list[tuple[str, str]] = []
    threshold = int(config.get("lifecycle.stale_run_threshold_seconds", 180))

    async for key in redis.scan_iter(match=pattern, count=200):
        run_id = await redis.get(key)
        if run_id is None:
            continue
        meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
        if meta is None or meta.status != "running":
            continue
        # A newly started replica is not evidence that every other worker died.
        if not is_stale_meta(meta, threshold_seconds=threshold):
            continue
        conversation_id = key[prefix_len:]
        marked = await mark_run_stale(
            redis,
            prefix=prefix,
            run_id=run_id,
            conversation_id=conversation_id,
            observed_last_event_at=meta.last_event_at or meta.started_at,
        )
        if not marked:
            continue
        recovered.append((conversation_id, run_id))
        logger.info(
            "Recovered stranded run {} on conversation {}",
            run_id,
            conversation_id,
        )

    if not recovered:
        return 0

    await _stamp_cubeloop_runs(recovered)
    await _fail_stranded_scheduled_runs([rid for _, rid in recovered])
    await _repair_stranded_threads([cid for cid, _ in recovered])
    await _kill_stranded_commands([rid for _, rid in recovered])

    logger.info("Startup recovery: {} stranded run(s) cleaned up", len(recovered))
    return len(recovered)


async def _kill_stranded_commands(run_ids: list[str]) -> None:
    if not run_ids:
        return
    await _stop_managed_stranded_runs(run_ids)
    try:
        from cubeplex.db.engine import async_session_maker
        from cubeplex.sandbox.command_coordinator import kill_run_commands, sandbox_from_row
    except Exception as exc:
        logger.warning("Could not import command killer for recovery: {}", exc)
        return
    try:
        async with async_session_maker() as session:

            async def _get(row):  # type: ignore[no-untyped-def]
                return await sandbox_from_row(row, session)

            for run_id in run_ids:
                try:
                    await kill_run_commands(session, run_id, get_sandbox=_get)
                except Exception as exc:
                    logger.warning("Failed to kill commands for stranded run {}: {}", run_id, exc)
    except Exception as exc:
        logger.warning("Could not kill leftover sandbox commands on recovery: {}", exc)


async def _stop_managed_stranded_runs(run_ids: list[str]) -> None:
    """Persist task-lifecycle Stop intents before legacy command cleanup."""
    from cubeplex.db.engine import async_session_maker
    from cubeplex.services.conversation_execution import ConversationExecutionService

    async with async_session_maker() as session:
        rows = list(
            (
                await session.execute(
                    select(
                        col(ConversationExecutionAdmission.id),
                        col(ConversationExecutionAdmission.org_id),
                        col(ConversationExecutionAdmission.workspace_id),
                        col(ConversationExecutionAdmission.run_id),
                    )
                    .where(col(ConversationExecutionAdmission.run_id).in_(run_ids))
                    .order_by(
                        col(ConversationExecutionAdmission.org_id),
                        col(ConversationExecutionAdmission.workspace_id),
                        col(ConversationExecutionAdmission.conversation_id),
                        col(ConversationExecutionAdmission.id),
                    )
                )
            ).all()
        )
    for admission_id, org_id, workspace_id, run_id in rows:
        if run_id is None:
            continue
        try:
            async with async_session_maker() as session:
                await ConversationExecutionService(
                    session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                ).stop_recovered_run(
                    admission_id=admission_id,
                    run_id=run_id,
                    now=datetime.now(UTC),
                )
                await session.commit()
        except Exception as exc:
            logger.warning("Failed to stop managed tasks for stranded run {}: {}", run_id, exc)


async def _stamp_cubeloop_runs(pairs: list[tuple[str, str]]) -> None:
    """Mark stranded cubepi_runs rows as completed so history is consistent."""
    from cubeplex.agents.checkpointer import shared_checkpointer

    try:
        async with shared_checkpointer() as cp:
            for thread_id, run_id in pairs:
                try:
                    await cp.mark_run_complete(thread_id, run_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to stamp cubepi_runs for {}/{}: {}",
                        thread_id,
                        run_id,
                        exc,
                    )
    except Exception as exc:
        logger.warning("Could not open checkpointer for recovery: {}", exc)


async def _fail_stranded_scheduled_runs(run_ids: list[str]) -> None:
    from cubeplex.schedules.completion_hook import (
        record_scheduled_run_terminal_state,
    )

    for run_id in run_ids:
        try:
            await record_scheduled_run_terminal_state(run_id=run_id, run_status="cancelled")
        except Exception as exc:
            logger.warning(
                "Failed to mark scheduled run {} as failed: {}",
                run_id,
                exc,
            )


async def _repair_stranded_threads(conversation_ids: list[str]) -> None:
    from cubeplex.streams.run_manager import _repair_dangling_tool_calls

    for conv_id in conversation_ids:
        try:
            await _repair_dangling_tool_calls(conv_id)
        except Exception as exc:
            logger.warning(
                "Failed to repair dangling tool_calls for {}: {}",
                conv_id,
                exc,
            )
