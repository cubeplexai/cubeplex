"""A restarted worker observes existing work, never starts the command again."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from opensandbox.exceptions import SandboxApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.models import BackgroundTask, BackgroundTaskEvent, SandboxCommand, UserSandbox
from cubeplex.models.background_task import TaskStopReason
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.services.background_task_coordinator import (
    BackgroundTaskCoordinator,
    ForegroundRecovery,
)
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e import test_command_instance_recovery as recovery_fixtures
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
    service,
)
from tests.e2e.test_command_instance_recovery import (
    remote_command,
)

reservation_context = reservation_fixtures.reservation_context
mock_encryption_backend = recovery_fixtures.mock_encryption_backend
remote = recovery_fixtures.remote


async def already_handed_off(task: BackgroundTask) -> ForegroundRecovery:
    raise AssertionError(f"background task {task.id} must not recover its foreground again")


async def started_task(
    session: AsyncSession, context: ReservationContext, *, receipt: bool = True
) -> tuple[BackgroundTask, SandboxCommand]:
    command_id = await remote_command(session, context)
    command = await session.get(SandboxCommand, command_id)
    assert command is not None and command.task_id is not None
    task = await session.get(BackgroundTask, command.task_id)
    assert task is not None and task.owner_token is not None
    lifecycle = service(session)
    assert await lifecycle.begin_start(task_id=task.id, owner_token=task.owner_token, now=NOW)
    if receipt:
        await lifecycle.register_start_receipt(
            task_id=task.id,
            start_token=task.owner_token,
            sandbox_instance_id=context.details.sandbox_instance_id,
            provider_ref="original-process",
            now=NOW,
        )
    await lifecycle.handoff_task(task_id=task.id, owner_token=task.owner_token, now=NOW)
    await session.commit()
    return task, command


async def test_restarted_workers_preserve_one_completion_and_never_restart_command(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    task, command = await started_task(db_session, reservation_context)
    _, raw, _ = remote
    raw.id = command.sandbox_instance_id
    raw.renew = AsyncMock()
    raw.commands.get_command_status = AsyncMock(
        return_value=SimpleNamespace(running=False, exit_code=0)
    )
    raw.commands.run = AsyncMock(side_effect=AssertionError("must not restart command"))
    for seconds in (31, 61):
        restarted = BackgroundTaskCoordinator(
            session_factory,
            SandboxManager(session_factory, mock_encryption_backend),
            resolve_foreground=already_handed_off,
            clock=lambda seconds=seconds: NOW + timedelta(seconds=seconds),
        )
        await restarted.reconcile_once()
    await db_session.refresh(task)
    await db_session.refresh(command)
    notices = list(
        (
            await db_session.execute(
                select(BackgroundTaskEvent).where(col(BackgroundTaskEvent.task_id) == task.id)
            )
        ).scalars()
    )
    assert task.state == "succeeded" and command.exit_code == 0
    assert command.provider_ref == "original-process"
    assert len(notices) == 1 and notices[0].state == "pending"
    assert command.log_state == "pending", "completion stays pending until C6 confirms logs"
    raw.commands.run.assert_not_awaited()


@pytest.mark.parametrize("status_failure", [False, True])
async def test_cancel_error_cannot_finish_task_or_free_capacity(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
    status_failure: bool,
) -> None:
    task, command = await started_task(db_session, reservation_context)
    await service(db_session).request_task_stop(
        task_id=task.id, reason=TaskStopReason.user_stop, now=NOW
    )
    await db_session.commit()
    _, raw, _ = remote
    raw.id = command.sandbox_instance_id
    raw.renew = AsyncMock()
    raw.commands.get_command_status = AsyncMock(
        return_value=SimpleNamespace(running=True, exit_code=None),
        side_effect=SandboxApiException("not found", status_code=404) if status_failure else None,
    )
    raw.commands.interrupt = AsyncMock(
        side_effect=SandboxApiException("not running", status_code=404)
    )
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    await coordinator.reconcile_once()
    await db_session.refresh(task)
    assert task.state == ("unknown" if status_failure else "running")
    assert task.finished_at is None and task.notifications_cancelled_at is not None
    assert command.provider_ref == "original-process"


async def test_provider_confirms_original_gone_without_touching_replacement(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    task, command = await started_task(db_session, reservation_context)
    sandbox = await db_session.get(UserSandbox, command.user_sandbox_id)
    assert sandbox is not None
    sandbox.sandbox_id = "replacement"
    await db_session.commit()
    control, _, connection = remote
    control.get_sandbox_info.side_effect = SandboxApiException("missing", status_code=404)
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    await coordinator.reconcile_once()
    await db_session.refresh(task)
    await db_session.refresh(command)
    assert task.state == "failed" and task.finished_at is not None
    assert command.log_state == "unavailable" and command.exit_code is None
    assert command.provider_ref == "original-process"
    assert control.get_sandbox_info.await_args.args == (command.sandbox_instance_id,)
    connection.assert_not_awaited()


async def test_start_receipt_loss_is_unknown_not_a_second_start(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    task, command = await started_task(db_session, reservation_context, receipt=False)
    _, raw, _ = remote
    raw.id = command.sandbox_instance_id
    raw.renew = AsyncMock()
    raw.commands.run = AsyncMock(side_effect=AssertionError("must not restart command"))
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    await coordinator.reconcile_once()
    await db_session.refresh(task)
    assert task.state == "unknown" and task.finished_at is None
    raw.commands.run.assert_not_awaited()


@pytest.mark.parametrize("notify", [True, False])
async def test_expired_deadline_is_not_reset_by_recovery_or_notification_policy(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
    notify: bool,
) -> None:
    task, command = await started_task(db_session, reservation_context)
    task.notify_on_complete = notify
    deadline = task.deadline_at
    assert deadline is not None
    await db_session.commit()
    _, raw, _ = remote
    raw.id = command.sandbox_instance_id
    raw.renew = AsyncMock()
    raw.commands.interrupt = AsyncMock()
    raw.commands.get_command_status = AsyncMock(
        side_effect=[
            SimpleNamespace(running=True, exit_code=None),
            SimpleNamespace(running=False, exit_code=137),
        ]
    )
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: deadline + timedelta(seconds=1),
    )
    await coordinator.reconcile_once()
    await db_session.refresh(task)
    await db_session.refresh(command)
    assert task.deadline_at == deadline and task.stop_reason == "deadline"
    assert task.notifications_cancelled_at is None
    assert task.state == "failed" and command.exit_code == 137
    notices = list(
        (
            await db_session.execute(
                select(BackgroundTaskEvent).where(col(BackgroundTaskEvent.task_id) == task.id)
            )
        ).scalars()
    )
    assert len(notices) == int(notify)
    raw.commands.interrupt.assert_awaited_once_with("original-process")


async def test_unsubmitted_reservation_can_finish_without_remote_execution(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    item = await reserve(db_session, reservation_context)
    assert item.task.owner_token is not None
    await service(db_session).handoff_task(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
    )
    await db_session.commit()
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: NOW + timedelta(seconds=31),
    )
    await coordinator.reconcile_once()
    await db_session.refresh(item.task)
    await db_session.refresh(item.command)
    assert item.task.state == "failed" and item.task.finished_at is not None
    assert item.command.status == "not_started" and item.command.provider_ref is None
    assert item.command.log_state == "complete"
    control, _, connection = remote
    control.get_sandbox_info.assert_not_awaited()
    connection.assert_not_awaited()


async def test_expired_observer_cannot_overwrite_new_owner_result(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    mock_encryption_backend: EncryptionBackend,
    remote: tuple[MagicMock, MagicMock, AsyncMock],
) -> None:
    from cubeplex.sandbox.base import ProcessSnapshot

    task, command = await started_task(db_session, reservation_context)
    entered, release = asyncio.Event(), asyncio.Event()
    now = NOW + timedelta(seconds=31)
    _, raw, _ = remote
    raw.id = command.sandbox_instance_id
    raw.renew = AsyncMock()

    async def slow_status(_ref: str) -> SimpleNamespace:
        entered.set()
        await release.wait()
        return SimpleNamespace(running=False, exit_code=0)

    raw.commands.get_command_status = slow_status
    coordinator = BackgroundTaskCoordinator(
        session_factory,
        SandboxManager(session_factory, mock_encryption_backend),
        resolve_foreground=already_handed_off,
        clock=lambda: now,
    )
    observing = asyncio.create_task(coordinator.reconcile_once())
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        now = NOW + timedelta(seconds=77)
        async with session_factory() as replacement:
            assert await service(replacement).claim_task(
                task_id=task.id,
                owner_token="replacement-owner",
                now=now,
                owner_until=now + timedelta(seconds=45),
            )
            await service(replacement).record_observation(
                task_id=task.id,
                owner_token="replacement-owner",
                now=now,
                snapshot=ProcessSnapshot(status="exited", exit_code=9),
                log_state="complete",
            )
            await replacement.commit()
    finally:
        release.set()
        await observing
    await db_session.refresh(task)
    await db_session.refresh(command)
    assert task.state == "failed" and command.exit_code == 9
    assert task.owner_token == "replacement-owner"
