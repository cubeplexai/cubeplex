"""Single-flight delivery of Postgres-backed HITL steering into CubePi."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cubepi.providers.base import TextContent, UserMessage
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils import uuid7

from cubeplex.models import SteeringMessage, SteeringMessageState
from cubeplex.repositories.steering_message import (
    SteeringMessageRepository,
    list_active_steering_for_reconciliation,
    purge_terminal_steering_tombstones,
)

HistorySteerLoader = Callable[[str], Awaitable[set[str]]]
MAINTENANCE_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class SteeringRunScope:
    org_id: str
    workspace_id: str
    conversation_id: str


def steering_message_to_cubepi(row: SteeringMessage) -> UserMessage:
    metadata: dict[str, Any] = {
        "steer_id": row.client_steer_id,
        "sender_user_id": row.sender_user_id,
    }
    if row.sender_display_name:
        metadata["sender_display_name"] = row.sender_display_name
    return UserMessage(
        content=[TextContent(text=row.content)],
        metadata=metadata,
    )


async def _load_checkpoint_steer_ids(conversation_id: str) -> set[str]:
    from cubeplex.agents.checkpointer import shared_checkpointer

    async with shared_checkpointer() as checkpointer:
        checkpoint = await checkpointer.load(conversation_id)
    if checkpoint is None:
        return set()
    steer_ids: set[str] = set()
    for message in checkpoint.messages:
        metadata = getattr(message, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        steer_id = metadata.get("steer_id")
        if isinstance(steer_id, str) and steer_id:
            steer_ids.add(steer_id)
    return steer_ids


class DurableSteeringCoordinator:
    """Owns delivery for the Agents registered in one RunManager process."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        history_loader: HistorySteerLoader = _load_checkpoint_steer_ids,
        poll_interval_seconds: float = 2.0,
        redis: Redis | None = None,
        redis_key_prefix: str | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._history_loader = history_loader
        self._poll_interval_seconds = poll_interval_seconds
        self._redis = redis
        self._redis_key_prefix = redis_key_prefix
        self._owner = f"steering-{uuid7()}"
        self._agents: dict[str, Any] = {}
        self._scopes: dict[str, SteeringRunScope] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_count = 0
        self._maintenance_cursor: tuple[datetime, str] | None = None

    def _repo(
        self,
        session: AsyncSession,
        scope: SteeringRunScope,
    ) -> SteeringMessageRepository:
        return SteeringMessageRepository(
            session,
            org_id=scope.org_id,
            workspace_id=scope.workspace_id,
        )

    async def register_and_drain(
        self,
        *,
        run_id: str,
        scope: SteeringRunScope,
        agent: Any,
    ) -> None:
        self._agents[run_id] = agent
        self._scopes[run_id] = scope
        self._locks.setdefault(run_id, asyncio.Lock())
        await self.drain(run_id)

    def unregister(self, run_id: str) -> None:
        self._agents.pop(run_id, None)
        self._scopes.pop(run_id, None)
        self._locks.pop(run_id, None)

    async def _repair_expired_claims(
        self,
        repo: SteeringMessageRepository,
        *,
        scope: SteeringRunScope,
        run_id: str,
    ) -> None:
        expired = await repo.list_expired_claims(run_id=run_id)
        foreign = [row for row in expired if row.delivery_owner != self._owner]
        if not foreign:
            return
        history_ids = await self._history_loader(scope.conversation_id)
        for row in foreign:
            if row.client_steer_id in history_ids:
                await repo.reconcile_terminal(
                    row_id=row.id,
                    state=SteeringMessageState.injected,
                )
            elif row.state == SteeringMessageState.cancel_requested:
                await repo.reconcile_terminal(
                    row_id=row.id,
                    state=SteeringMessageState.cancelled,
                )
            else:
                await repo.requeue_expired_claim(row_id=row.id)

    async def _process_owned_cancel_requests(
        self,
        repo: SteeringMessageRepository,
        *,
        scope: SteeringRunScope,
        run_id: str,
        agent: Any,
    ) -> None:
        rows = await repo.list_owned_cancel_requests(run_id=run_id, owner=self._owner)
        history_ids: set[str] | None = None
        for row in rows:
            if agent.cancel_steer(row.client_steer_id):
                await repo.mark_owned_cancelled(row_id=row.id, owner=self._owner)
                continue
            if history_ids is None:
                history_ids = await self._history_loader(scope.conversation_id)
            if row.client_steer_id in history_ids:
                await repo.reconcile_terminal(
                    row_id=row.id,
                    state=SteeringMessageState.injected,
                )

    async def drain(self, run_id: str) -> None:
        agent = self._agents.get(run_id)
        scope = self._scopes.get(run_id)
        if agent is None or scope is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            async with self._session_maker() as session:
                repo = self._repo(session, scope)
                await self._process_owned_cancel_requests(
                    repo,
                    scope=scope,
                    run_id=run_id,
                    agent=agent,
                )
                await self._repair_expired_claims(repo, scope=scope, run_id=run_id)
                claimed = await repo.claim_queued(run_id=run_id, owner=self._owner)
                await session.commit()

            for row in claimed:
                try:
                    agent.steer(steering_message_to_cubepi(row))
                except Exception:
                    logger.opt(exception=True).warning(
                        "durable steering delivery failed synchronously for row {}",
                        row.id,
                    )
                    async with self._session_maker() as session:
                        repo = self._repo(session, scope)
                        await repo.return_claim_to_queue(row_id=row.id, owner=self._owner)
                        await session.commit()

    async def acknowledge_injected(self, run_id: str, client_steer_id: str) -> None:
        scope = self._scopes.get(run_id)
        if scope is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            try:
                async with self._session_maker() as session:
                    repo = self._repo(session, scope)
                    row = await repo.get_by_client_id(
                        conversation_id=scope.conversation_id,
                        client_steer_id=client_steer_id,
                    )
                    if row is not None and row.run_id == run_id:
                        await repo.mark_owned_injected(row_id=row.id, owner=self._owner)
                    await session.commit()
            except Exception:
                logger.opt(exception=True).warning(
                    "durable steering acknowledgement failed for run {}",
                    run_id,
                )

    async def cancel_dispatched(self, run_id: str, client_steer_id: str) -> None:
        scope = self._scopes.get(run_id)
        agent = self._agents.get(run_id)
        if scope is None or agent is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            async with self._session_maker() as session:
                repo = self._repo(session, scope)
                row = await repo.get_by_client_id(
                    conversation_id=scope.conversation_id,
                    client_steer_id=client_steer_id,
                )
                if (
                    row is None
                    or row.run_id != run_id
                    or row.state != SteeringMessageState.cancel_requested
                ):
                    return
                if agent.cancel_steer(client_steer_id):
                    await repo.mark_owned_cancelled(row_id=row.id, owner=self._owner)
                else:
                    history_ids = await self._history_loader(scope.conversation_id)
                    if client_steer_id in history_ids:
                        await repo.reconcile_terminal(
                            row_id=row.id,
                            state=SteeringMessageState.injected,
                        )
                await session.commit()

    async def finalize_run(
        self,
        run_id: str,
        *,
        scope: SteeringRunScope | None = None,
    ) -> None:
        scope = scope or self._scopes.get(run_id)
        if scope is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            try:
                history_ids = await self._history_loader(scope.conversation_id)
                async with self._session_maker() as session:
                    repo = self._repo(session, scope)
                    active = await repo.list_active_for_run(run_id)
                    for row in active:
                        if row.client_steer_id in history_ids:
                            await repo.reconcile_terminal(
                                row_id=row.id,
                                state=SteeringMessageState.injected,
                            )
                    await repo.fail_active_for_run(run_id)
                    await session.commit()
            except Exception:
                logger.opt(exception=True).warning(
                    "durable steering finalization failed for run {}",
                    run_id,
                )

    async def poll_once(self) -> None:
        for run_id in tuple(self._agents):
            await self.drain(run_id)

    async def maintain_once(self) -> None:
        """Repair terminal rows and purge old tombstones in bounded batches."""
        if self._redis is None or self._redis_key_prefix is None:
            return
        from cubeplex.agents.checkpointer import shared_checkpointer
        from cubeplex.streams.run_events import get_run_meta

        async with self._session_maker() as session:
            rows = await list_active_steering_for_reconciliation(
                session,
                limit=MAINTENANCE_BATCH_SIZE,
                after=self._maintenance_cursor,
            )
            if not rows and self._maintenance_cursor is not None:
                self._maintenance_cursor = None
                rows = await list_active_steering_for_reconciliation(
                    session,
                    limit=MAINTENANCE_BATCH_SIZE,
                )
            next_cursor = (rows[-1].updated_at, rows[-1].id) if rows else self._maintenance_cursor
            history_by_conversation: dict[str, set[str]] = {}
            pending_by_conversation: dict[str, str | None] = {}
            finalized_runs: set[tuple[str, str, str]] = set()
            async with shared_checkpointer() as checkpointer:
                for row in rows:
                    if row.run_id in self._agents:
                        continue
                    history_ids = history_by_conversation.get(row.conversation_id)
                    if history_ids is None:
                        history_ids = await self._history_loader(row.conversation_id)
                        history_by_conversation[row.conversation_id] = history_ids
                    repo = SteeringMessageRepository(
                        session,
                        org_id=row.org_id,
                        workspace_id=row.workspace_id,
                    )
                    if row.client_steer_id in history_ids:
                        await repo.reconcile_terminal(
                            row_id=row.id,
                            state=SteeringMessageState.injected,
                        )
                        continue
                    if row.conversation_id not in pending_by_conversation:
                        pending_by_conversation[
                            row.conversation_id
                        ] = await checkpointer.load_pending_run_id(row.conversation_id)
                    pending_run_id = pending_by_conversation[row.conversation_id]
                    meta = await get_run_meta(
                        self._redis,
                        prefix=self._redis_key_prefix,
                        run_id=row.run_id,
                    )
                    if meta is not None and meta.status in ("running", "paused_hitl"):
                        continue
                    if meta is None and pending_run_id == row.run_id:
                        continue
                    run_key = (row.org_id, row.workspace_id, row.run_id)
                    if run_key not in finalized_runs:
                        await repo.fail_active_for_run(row.run_id)
                        finalized_runs.add(run_key)
            await purge_terminal_steering_tombstones(session, limit=100)
            await session.commit()
            self._maintenance_cursor = next_cursor

    async def _poll_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval_seconds)
                try:
                    await self.poll_once()
                    self._poll_count += 1
                    if self._poll_count % max(1, int(30 / self._poll_interval_seconds)) == 0:
                        await self.maintain_once()
                except Exception:
                    logger.opt(exception=True).warning("durable steering fallback poll failed")
        except asyncio.CancelledError:
            raise

    def start(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_loop(),
                name="durable-steering-poll",
            )

    async def stop(self) -> None:
        task = self._poll_task
        self._poll_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
