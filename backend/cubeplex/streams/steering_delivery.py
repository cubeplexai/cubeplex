"""Single-flight delivery of Postgres-backed HITL steering into CubeLoop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from cubeloop.providers.base import TextContent, UserMessage
from cubeloop.session.input import InputEnvelope, InputReceipt
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
ACKNOWLEDGEMENT_ATTEMPTS = 3
ACKNOWLEDGEMENT_RETRY_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class SteeringRunScope:
    org_id: str
    workspace_id: str
    conversation_id: str


class SteeringSessionProtocol(Protocol):
    def submit_input(self, envelope: InputEnvelope) -> InputReceipt: ...

    def cancel_input(self, input_id: str) -> InputReceipt: ...


def _is_checkpoint_committed(receipt: InputReceipt) -> bool:
    return receipt.status == "committed" and receipt.durability == "checkpoint"


def steering_message_to_cubeloop(row: SteeringMessage) -> UserMessage:
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
    """Owns delivery for Sessions registered in one RunManager process."""

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
        self._sessions: dict[str, SteeringSessionProtocol] = {}
        self._scopes: dict[str, SteeringRunScope] = {}
        self._claim_tokens: dict[str, str | None] = {}
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
        session: SteeringSessionProtocol,
        claim_token: str | None = None,
    ) -> None:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if not await self._claim_is_current(run_id, claim_token):
                return
            await self._repair_checkpointed_owned_claims(run_id=run_id, scope=scope)
            self._sessions[run_id] = session
            self._scopes[run_id] = scope
            self._claim_tokens[run_id] = claim_token
        await self.drain(run_id)

    async def _claim_is_current(self, run_id: str, claim_token: str | None) -> bool:
        if self._redis is None or self._redis_key_prefix is None:
            return True
        from cubeplex.streams.hitl_resume import get_resume_claim_token

        distributed_claim = await get_resume_claim_token(
            self._redis,
            prefix=self._redis_key_prefix,
            run_id=run_id,
        )
        return distributed_claim == claim_token

    async def _repair_checkpointed_owned_claims(
        self,
        *,
        run_id: str,
        scope: SteeringRunScope,
    ) -> None:
        async with self._session_maker() as session:
            repo = self._repo(session, scope)
            rows = await repo.list_owned_claims(run_id=run_id, owner=self._owner)
            if not rows:
                return
            history_ids = await self._history_loader(scope.conversation_id)
            for row in rows:
                if row.client_steer_id in history_ids:
                    await repo.mark_owned_injected(row_id=row.id, owner=self._owner)
            await session.commit()

    async def unregister(
        self,
        run_id: str,
        *,
        session: SteeringSessionProtocol,
        requeue_owned: bool = False,
    ) -> None:
        if self._sessions.get(run_id) is not session:
            return
        lock = self._locks.get(run_id)
        if lock is None:
            if self._sessions.get(run_id) is not session:
                return
            self._sessions.pop(run_id, None)
            self._scopes.pop(run_id, None)
            self._claim_tokens.pop(run_id, None)
            return
        async with lock:
            if self._sessions.get(run_id) is not session:
                return
            scope = self._scopes.get(run_id)
            if requeue_owned and scope is not None:
                await self._reconcile_owned_before_pause(
                    run_id=run_id,
                    scope=scope,
                    session=session,
                )
            self._sessions.pop(run_id, None)
            self._scopes.pop(run_id, None)
            self._claim_tokens.pop(run_id, None)
            if self._locks.get(run_id) is lock:
                self._locks.pop(run_id, None)

    async def _reconcile_owned_before_pause(
        self,
        *,
        run_id: str,
        scope: SteeringRunScope,
        session: SteeringSessionProtocol,
    ) -> None:
        async with self._session_maker() as db_session:
            repo = self._repo(db_session, scope)
            rows = await repo.list_owned_claims(run_id=run_id, owner=self._owner)
            history_ids: set[str] | None = None
            for row in rows:
                receipt = session.cancel_input(row.client_steer_id)
                if row.state == SteeringMessageState.cancel_requested:
                    if receipt.status == "cancelled":
                        await repo.mark_owned_cancelled(row_id=row.id, owner=self._owner)
                        continue
                    if _is_checkpoint_committed(receipt):
                        await repo.reconcile_terminal(
                            row_id=row.id,
                            state=SteeringMessageState.injected,
                        )
                        continue
                    if receipt.status == "committed":
                        # Session memory has consumed the input, but its
                        # suspension checkpoint has not committed yet. Keep
                        # ownership until that checkpoint or later history
                        # reconciliation proves the durable outcome.
                        continue
                    if history_ids is None:
                        history_ids = await self._history_loader(scope.conversation_id)
                    await repo.reconcile_terminal(
                        row_id=row.id,
                        state=(
                            SteeringMessageState.injected
                            if row.client_steer_id in history_ids
                            else SteeringMessageState.cancelled
                        ),
                    )
                    continue
                if _is_checkpoint_committed(receipt):
                    await repo.reconcile_terminal(
                        row_id=row.id,
                        state=SteeringMessageState.injected,
                    )
                    continue
                if receipt.status == "committed":
                    # Requeueing a memory-committed input would submit it a
                    # second time after the upcoming suspension checkpoint.
                    continue
                if receipt.status != "cancelled":
                    if history_ids is None:
                        history_ids = await self._history_loader(scope.conversation_id)
                    if row.client_steer_id in history_ids:
                        await repo.reconcile_terminal(
                            row_id=row.id,
                            state=SteeringMessageState.injected,
                        )
                        continue
                await repo.return_claim_to_queue(row_id=row.id, owner=self._owner)
            await db_session.commit()

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
        session: SteeringSessionProtocol,
        claim_token: str | None,
    ) -> None:
        rows = await repo.list_owned_cancel_requests(run_id=run_id, owner=self._owner)
        history_ids: set[str] | None = None
        for row in rows:
            if not await self._claim_is_current(run_id, claim_token):
                return
            receipt = session.cancel_input(row.client_steer_id)
            if receipt.status == "cancelled":
                await repo.mark_owned_cancelled(row_id=row.id, owner=self._owner)
                continue
            if _is_checkpoint_committed(receipt):
                await repo.reconcile_terminal(
                    row_id=row.id,
                    state=SteeringMessageState.injected,
                )
                continue
            if history_ids is None:
                history_ids = await self._history_loader(scope.conversation_id)
            if row.client_steer_id in history_ids:
                await repo.reconcile_terminal(
                    row_id=row.id,
                    state=SteeringMessageState.injected,
                )

    async def drain(self, run_id: str) -> None:
        execution_session = self._sessions.get(run_id)
        scope = self._scopes.get(run_id)
        claim_token = self._claim_tokens.get(run_id)
        if execution_session is None or scope is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if (
                self._locks.get(run_id) is not lock
                or self._sessions.get(run_id) is not execution_session
                or self._scopes.get(run_id) is not scope
                or self._claim_tokens.get(run_id) != claim_token
            ):
                return
            if not await self._claim_is_current(run_id, claim_token):
                return
            assert execution_session is not None
            async with self._session_maker() as session:
                repo = self._repo(session, scope)
                await self._process_owned_cancel_requests(
                    repo,
                    scope=scope,
                    run_id=run_id,
                    session=execution_session,
                    claim_token=claim_token,
                )
                if not await self._claim_is_current(run_id, claim_token):
                    return
                await self._repair_expired_claims(repo, scope=scope, run_id=run_id)
                if not await self._claim_is_current(run_id, claim_token):
                    return
                claimed = await repo.claim_queued(run_id=run_id, owner=self._owner)
                await session.commit()

            checkpoint_committed_ids: list[str] = []
            for index, row in enumerate(claimed):
                try:
                    if not await self._claim_is_current(run_id, claim_token):
                        raise RuntimeError("resume claim changed before durable input admission")
                    receipt = execution_session.submit_input(
                        InputEnvelope(
                            input_id=row.client_steer_id,
                            message=steering_message_to_cubeloop(row),
                            mode="steer",
                        )
                    )
                    if receipt.status not in ("queued", "committed"):
                        raise RuntimeError(f"input admission returned {receipt.status}")
                    if _is_checkpoint_committed(receipt):
                        checkpoint_committed_ids.append(row.id)
                except Exception:
                    logger.opt(exception=True).warning(
                        "durable steering delivery failed synchronously for row {}",
                        row.id,
                    )
                    async with self._session_maker() as session:
                        repo = self._repo(session, scope)
                        for undelivered in claimed[index:]:
                            await repo.return_claim_to_queue(
                                row_id=undelivered.id,
                                owner=self._owner,
                            )
                        await session.commit()
                    break
            if checkpoint_committed_ids:
                async with self._session_maker() as session:
                    repo = self._repo(session, scope)
                    for row_id in checkpoint_committed_ids:
                        await repo.mark_owned_injected(row_id=row_id, owner=self._owner)
                    await session.commit()

    async def acknowledge_injected(
        self,
        run_id: str,
        client_steer_id: str,
        *,
        scope: SteeringRunScope | None = None,
    ) -> None:
        registered_scope = self._scopes.get(run_id)
        resolved_scope = registered_scope or scope
        if resolved_scope is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        try:
            async with lock:
                for attempt in range(ACKNOWLEDGEMENT_ATTEMPTS):
                    try:
                        await self._acknowledge_injected_once(
                            run_id=run_id,
                            client_steer_id=client_steer_id,
                            scope=resolved_scope,
                        )
                        break
                    except Exception:
                        if attempt + 1 == ACKNOWLEDGEMENT_ATTEMPTS:
                            raise
                        logger.opt(exception=True).warning(
                            "durable steering acknowledgement retry {} for run {}",
                            attempt + 1,
                            run_id,
                        )
                        await asyncio.sleep(ACKNOWLEDGEMENT_RETRY_SECONDS)
        finally:
            if (
                registered_scope is None
                and self._scopes.get(run_id) is None
                and self._locks.get(run_id) is lock
            ):
                self._locks.pop(run_id, None)

    async def _acknowledge_injected_once(
        self,
        *,
        run_id: str,
        client_steer_id: str,
        scope: SteeringRunScope,
    ) -> None:
        async with self._session_maker() as session:
            repo = self._repo(session, scope)
            row = await repo.get_by_client_id(
                conversation_id=scope.conversation_id,
                client_steer_id=client_steer_id,
            )
            if row is not None and row.run_id == run_id:
                await repo.mark_owned_injected(row_id=row.id, owner=self._owner)
            await session.commit()

    async def cancel_dispatched(self, run_id: str, client_steer_id: str) -> None:
        scope = self._scopes.get(run_id)
        execution_session = self._sessions.get(run_id)
        claim_token = self._claim_tokens.get(run_id)
        if scope is None or execution_session is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if (
                self._sessions.get(run_id) is not execution_session
                or self._scopes.get(run_id) is not scope
                or self._claim_tokens.get(run_id) != claim_token
                or not await self._claim_is_current(run_id, claim_token)
            ):
                return
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
                if not await self._claim_is_current(run_id, claim_token):
                    return
                receipt = execution_session.cancel_input(client_steer_id)
                if receipt.status == "cancelled":
                    await repo.mark_owned_cancelled(row_id=row.id, owner=self._owner)
                elif _is_checkpoint_committed(receipt):
                    await repo.reconcile_terminal(
                        row_id=row.id,
                        state=SteeringMessageState.injected,
                    )
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
                    await repo.finalize_active_for_run(run_id)
                    await session.commit()
            except Exception:
                logger.opt(exception=True).warning(
                    "durable steering finalization failed for run {}",
                    run_id,
                )

    async def poll_once(self) -> None:
        for run_id in tuple(self._sessions):
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
                    if row.run_id in self._sessions:
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
                    if pending_run_id == row.run_id and (meta is None or meta.status == "stale"):
                        continue
                    run_key = (row.org_id, row.workspace_id, row.run_id)
                    if run_key not in finalized_runs:
                        await repo.finalize_active_for_run(row.run_id)
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
