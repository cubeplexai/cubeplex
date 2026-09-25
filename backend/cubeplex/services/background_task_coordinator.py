"""Durable command observation; activation is gated by the lifecycle cutover."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.config import config
from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    TERMINAL_TASK_STATES,
    BackgroundTask,
)
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandStatus
from cubeplex.sandbox.base import ProcessHandle, SandboxError, SandboxInstanceGoneError
from cubeplex.sandbox.command_adapter import CommandAdapter
from cubeplex.sandbox.log_io import append_output
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.services.background_task_lifecycle import (
    ForegroundResultEvidence,
    LogState,
    TaskOwnerLostError,
)
from cubeplex.services.background_tasks import BackgroundTaskService

ForegroundRecovery = ForegroundResultEvidence | Literal["pending", "not_delivered"]
OWNER_LEASE = timedelta(seconds=45)
POLL_INTERVAL = 15


def utc_now() -> datetime:
    return datetime.now(UTC)


class ForegroundCheckpointer(Protocol):
    async def load(self, thread_id: str) -> Any: ...

    async def load_pending_run_id(self, thread_id: str) -> str | None: ...

    async def claim_run(self, thread_id: str, run_id: str) -> None: ...

    async def mark_run_complete(self, thread_id: str, run_id: str) -> None: ...


def _has_checkpointed_result(task: BackgroundTask, checkpoint: Any) -> bool:
    from cubeloop.providers.base import ToolResultMessage

    if checkpoint is None:
        return False
    return any(
        isinstance(message, ToolResultMessage)
        and message.run_id == task.originating_run_id
        and message.tool_call_id == task.tool_call_id
        for message in checkpoint.messages
    )


async def _resolve_with_checkpointer(
    task: BackgroundTask,
    *,
    checkpointer: ForegroundCheckpointer,
    redis: Any,
    redis_key_prefix: str,
    load_run_meta: Callable[..., Awaitable[Any]],
) -> ForegroundRecovery:
    from cubeloop.checkpointer.exceptions import (
        RunAlreadyClaimedError,
        RunAlreadyCompletedError,
        RunNotClaimedError,
    )

    checkpoint = await checkpointer.load(task.conversation_id)
    if _has_checkpointed_result(task, checkpoint):
        return ForegroundResultEvidence(
            run_id=task.originating_run_id,
            tool_call_id=task.tool_call_id,
            agent_id=task.agent_id,
        )
    pending_run_id = await checkpointer.load_pending_run_id(task.conversation_id)
    meta = await load_run_meta(
        redis,
        prefix=redis_key_prefix,
        run_id=task.originating_run_id,
    )
    if pending_run_id == task.originating_run_id or (
        meta is not None and meta.status in ("running", "paused_hitl")
    ):
        return "pending"
    try:
        await checkpointer.mark_run_complete(task.conversation_id, task.originating_run_id)
    except RunNotClaimedError:
        try:
            await checkpointer.claim_run(task.conversation_id, task.originating_run_id)
        except (RunAlreadyClaimedError, RunAlreadyCompletedError):
            pass
        try:
            await checkpointer.mark_run_complete(task.conversation_id, task.originating_run_id)
        except RunAlreadyCompletedError:
            pass
    except RunAlreadyCompletedError:
        pass
    checkpoint = await checkpointer.load(task.conversation_id)
    if _has_checkpointed_result(task, checkpoint):
        return ForegroundResultEvidence(
            run_id=task.originating_run_id,
            tool_call_id=task.tool_call_id,
            agent_id=task.agent_id,
        )
    return "not_delivered"


async def resolve_foreground_checkpoint(
    task: BackgroundTask,
    *,
    redis: Any,
    redis_key_prefix: str,
    checkpointer: ForegroundCheckpointer | None = None,
    load_run_meta: Callable[..., Awaitable[Any]] | None = None,
) -> ForegroundRecovery:
    """Fence an abandoned run before deciding whether its result was delivered."""
    if load_run_meta is None:
        from cubeplex.streams.run_events import get_run_meta

        load_run_meta = get_run_meta
    if checkpointer is not None:
        return await _resolve_with_checkpointer(
            task,
            checkpointer=checkpointer,
            redis=redis,
            redis_key_prefix=redis_key_prefix,
            load_run_meta=load_run_meta,
        )
    from cubeplex.agents.checkpointer import shared_checkpointer

    async with shared_checkpointer() as shared:
        return await _resolve_with_checkpointer(
            task,
            checkpointer=shared,
            redis=redis,
            redis_key_prefix=redis_key_prefix,
            load_run_meta=load_run_meta,
        )


class BackgroundTaskCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sandbox_manager: SandboxManager,
        *,
        resolve_foreground: Callable[[BackgroundTask], Awaitable[ForegroundRecovery]],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_factory = session_factory
        self.sandbox_manager = sandbox_manager
        self.resolve_foreground = resolve_foreground
        self.clock = clock
        self._worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("background task scan failed")
            await asyncio.sleep(POLL_INTERVAL)

    async def reconcile_once(self) -> int:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        col(BackgroundTask.id),
                        col(BackgroundTask.org_id),
                        col(BackgroundTask.workspace_id),
                    )
                    .join(SandboxCommand, col(SandboxCommand.task_id) == col(BackgroundTask.id))
                    .where(
                        or_(
                            col(BackgroundTask.owner_until).is_(None),
                            col(BackgroundTask.owner_until) <= self.clock(),
                        ),
                        or_(
                            col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
                            col(SandboxCommand.log_state).in_(("pending", "retrying")),
                            and_(
                                col(BackgroundTask.backgrounded_at).is_(None),
                                col(BackgroundTask.foreground_result_delivered_at).is_(None),
                                col(BackgroundTask.notifications_cancelled_at).is_(None),
                            ),
                        ),
                    )
                    .order_by(
                        func.coalesce(BackgroundTask.owner_until, BackgroundTask.created_at),
                        col(BackgroundTask.id),
                    )
                    .limit(32)
                )
            ).all()
        limit = asyncio.Semaphore(4)

        async def manage(task_id: str, org_id: str, workspace_id: str) -> bool:
            async with limit:
                try:
                    return await self._reconcile(task_id, org_id=org_id, workspace_id=workspace_id)
                except Exception:
                    logger.exception("background task reconciliation failed for {}", task_id)
                    return False

        return sum(await asyncio.gather(*(manage(*row) for row in rows)))

    async def _reconcile(self, task_id: str, *, org_id: str, workspace_id: str) -> bool:
        token = str(uuid4())

        def service(session: AsyncSession) -> BackgroundTaskService:
            return BackgroundTaskService(session, org_id=org_id, workspace_id=workspace_id)

        async with self.session_factory() as session:
            now = self.clock()
            if not await service(session).claim_task(
                task_id=task_id, owner_token=token, now=now, owner_until=now + OWNER_LEASE
            ):
                return False
            task, command = await service(session).prepare_observation(
                task_id=task_id, owner_token=token, now=now
            )
            await session.commit()

        async def check_owner() -> None:
            async with self.session_factory() as session:
                now = self.clock()
                await service(session).renew_owner(
                    task_id=task_id, owner_token=token, now=now, owner_until=now + OWNER_LEASE
                )
                await session.commit()

        try:
            if (
                command.start_requested_at is None
                and command.provider_ref is None
                and command.status
                in (SandboxCommandStatus.starting.value, SandboxCommandStatus.not_started.value)
            ):
                async with self.session_factory() as session:
                    await service(session).record_not_started(
                        task_id=task_id, owner_token=token, now=self.clock()
                    )
                    await session.commit()
            elif task.state in TERMINAL_TASK_STATES and (
                command.sandbox_instance_id is None or command.provider_ref is None
            ):
                async with self.session_factory() as session:
                    await service(session).record_unrecoverable_terminal_log(
                        task_id=task_id, owner_token=token, now=self.clock()
                    )
                    await session.commit()
            else:
                await check_owner()
                async with self.sandbox_manager.connect_command_instance(
                    command_id=command.id, org_id=org_id, workspace_id=workspace_id
                ) as sandbox:
                    await check_owner()
                    try:
                        async with asyncio.timeout(10):
                            await sandbox.renew(int(config.get("sandbox.ttl", 600)))
                    except (SandboxError, TimeoutError):
                        logger.warning("original sandbox keepalive failed for task {}", task_id)
                    if command.provider_ref is None:
                        raise SandboxError("start was submitted but its receipt is still unknown")
                    assert command.sandbox_instance_id is not None
                    handle = ProcessHandle(
                        command.id,
                        command.provider_ref,
                        log_cursor=command.log_cursor,
                        deadline_at=task.deadline_at,
                    )
                    observed = await CommandAdapter(
                        sandbox, sandbox_instance_id=command.sandbox_instance_id
                    ).observe_and_stop(
                        handle,
                        stop_requested=task.stop_requested_at is not None,
                        check_owner=check_owner,
                    )
                    confirmed_log_cursor: str | None = None
                    async with self.session_factory() as session:
                        if observed.snapshot is None:
                            await service(session).record_observation_failure(
                                task_id=task_id,
                                owner_token=token,
                                now=self.clock(),
                                message=observed.error or "process state is unknown",
                            )
                        else:
                            snapshot = observed.snapshot
                            log_state: LogState = cast(LogState, command.log_state)
                            if observed.logs_read:
                                data_written = True
                                if snapshot.new_output or snapshot.status != "running":
                                    # A crash after this write but before the cursor commit may
                                    # repeat the tail. Never skip output whose DB ack is unknown.
                                    appended = await append_output(
                                        sandbox,
                                        command.log_path,
                                        snapshot.new_output,
                                    )
                                    data_written = appended.data_written
                                    if data_written and not appended.cleanup_done:
                                        logger.warning(
                                            "command log chunk cleanup remains for task {}",
                                            task_id,
                                        )
                                if data_written:
                                    confirmed_log_cursor = snapshot.log_cursor
                                    log_state = (
                                        "pending" if snapshot.status == "running" else "complete"
                                    )
                                else:
                                    log_state = "retrying"
                            elif command.log_state not in ("complete", "unavailable"):
                                log_state = "retrying"
                            await service(session).record_observation(
                                task_id=task_id,
                                owner_token=token,
                                now=self.clock(),
                                snapshot=snapshot,
                                log_state=log_state,
                                expected_log_cursor=command.log_cursor,
                                confirmed_log_cursor=confirmed_log_cursor,
                            )
                        await session.commit()
                    if confirmed_log_cursor is not None:
                        await sandbox.acknowledge_output(handle, confirmed_log_cursor)
        except TaskOwnerLostError:
            return False
        except SandboxInstanceGoneError as exc:
            async with self.session_factory() as session:
                await service(session).record_environment_gone(
                    task_id=task_id,
                    owner_token=token,
                    sandbox_instance_id=exc.sandbox_instance_id,
                    now=self.clock(),
                )
                await session.commit()
        except Exception as exc:
            logger.warning("background task observation failed for {}: {}", task_id, exc)
            async with self.session_factory() as session:
                await service(session).record_observation_failure(
                    task_id=task_id, owner_token=token, now=self.clock(), message=str(exc)
                )
                await session.commit()

        try:
            await check_owner()
            async with self.session_factory() as session:
                recovering_task = await service(session).tasks.get(task_id)
            if (
                recovering_task is not None
                and recovering_task.backgrounded_at is None
                and recovering_task.foreground_result_delivered_at is None
                and recovering_task.notifications_cancelled_at is None
            ):
                # The host must fence the original run attempt and inspect its checkpoint.
                async with asyncio.timeout(10):
                    recovery = await self.resolve_foreground(recovering_task)
                async with self.session_factory() as session:
                    if isinstance(recovery, ForegroundResultEvidence):
                        await service(session).record_foreground_delivery(
                            task_id=task_id, owner_token=token, now=self.clock(), evidence=recovery
                        )
                    elif recovery == "not_delivered":
                        await service(session).handoff_task(
                            task_id=task_id, owner_token=token, now=self.clock()
                        )
                    await session.commit()
        except TaskOwnerLostError:
            return False
        except Exception:
            logger.exception("foreground checkpoint recovery remains pending for {}", task_id)
        finally:
            with suppress(TaskOwnerLostError):
                async with self.session_factory() as session:
                    now = self.clock()
                    await service(session).defer_owner(
                        task_id=task_id,
                        owner_token=token,
                        now=now,
                        retry_at=now + timedelta(seconds=POLL_INTERVAL),
                    )
                    await session.commit()
        return True
