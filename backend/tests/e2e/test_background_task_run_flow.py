"""Sandbox tools reserve and hand work to the durable task lifecycle."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.middleware import sandbox as sandbox_module
from cubeplex.middleware.sandbox import (
    SandboxMiddleware,
    _ExecuteArgs,
    _KillExecuteArgs,
    _MonitorArgs,
    _ReservedCommand,
)
from cubeplex.models import BackgroundTask, SandboxCommand
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot, Sandbox
from cubeplex.sandbox.command_coordinator import kill_sandbox_commands
from cubeplex.services.background_tasks import BackgroundTaskService
from cubeplex.services.conversation_execution import ConversationExecutionService
from cubeplex.streams.recovery import _kill_stranded_commands
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import ReservationContext

reservation_context = reservation_fixtures.reservation_context


def _sandbox(context: ReservationContext) -> MagicMock:
    sandbox = MagicMock(spec=Sandbox)
    sandbox.id = context.details.sandbox_instance_id
    sandbox.user_sandbox_id = context.details.user_sandbox_id
    sandbox.workdir = "/workspace"
    sandbox.supports_background.return_value = True
    sandbox.ensure_created = AsyncMock()
    sandbox.upload = AsyncMock()

    async def _start(
        _command: str,
        *,
        timeout: int | None = None,
        on_started: Any = None,
    ) -> ProcessHandle:
        del timeout
        if on_started is not None:
            await on_started("provider-process")
        return ProcessHandle(command_id="", provider_ref="provider-process")

    sandbox.start = AsyncMock(side_effect=_start)
    return sandbox


async def test_explicit_background_command_returns_task_and_hands_off(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    middleware = SandboxMiddleware(
        sandbox=_sandbox(reservation_context),
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    execute = next(tool for tool in middleware.tools if tool.name == "execute")

    result = await execute.execute(
        "tool-background",
        _ExecuteArgs(
            command="build project",
            description="Build project",
            background=True,
        ),
    )

    assert isinstance(result.details, dict)
    task_id = result.details["task_id"]
    command_id = result.details["command_id"]
    assert result.details["deadline_at"] is not None
    assert result.details["notification"] == "once"
    assert result.details["result_pending"] is True
    task = await db_session.get(BackgroundTask, task_id)
    command = await db_session.get(SandboxCommand, command_id)
    assert task is not None and command is not None
    assert command.task_id == task.id
    assert command.provider_ref == "provider-process"
    assert task.backgrounded_at is not None
    assert task.owner_token is None
    assert middleware._live_commands == {}

    repeated = await execute.execute(
        "tool-background",
        _ExecuteArgs(
            command="build project",
            description="Build project",
            background=True,
        ),
    )
    assert repeated.details["task_id"] == task_id  # type: ignore[index]
    assert repeated.details["command_id"] == command_id  # type: ignore[index]
    middleware.sandbox.start.assert_awaited_once()

    kill = next(tool for tool in middleware.tools if tool.name == "kill_execute")
    stopping = await kill.execute(
        "tool-stop",
        _KillExecuteArgs(command_id=str(command_id)),
    )
    await db_session.refresh(task)
    assert stopping.details == {"status": "stopping", "command_id": command_id}
    assert task.stop_requested_at is not None
    assert task.notifications_cancelled_at is not None


async def test_foreground_budget_hands_running_command_to_task_coordinator(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_module, "AUTO_BACKGROUND_SECONDS", 0)
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    sandbox = _sandbox(reservation_context)
    sandbox.poll = AsyncMock(return_value=ProcessSnapshot(status="running", log_cursor="cursor-7"))
    middleware = SandboxMiddleware(
        sandbox=sandbox,
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    execute = next(tool for tool in middleware.tools if tool.name == "execute")

    result = await execute.execute(
        "tool-auto-background",
        _ExecuteArgs(command="build project", description="Build project"),
    )

    assert isinstance(result.details, dict)
    assert result.details["deadline_at"] is not None
    assert result.details["notification"] == "once"
    task = await db_session.get(BackgroundTask, result.details["task_id"])
    command = await db_session.get(SandboxCommand, result.details["command_id"])
    assert task is not None and command is not None
    assert task.state == "running"
    assert task.backgrounded_at is not None
    assert task.owner_token is None
    assert command.log_cursor == "cursor-7"
    assert middleware._live_commands == {}


async def test_provider_start_failure_stays_recoverable(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    sandbox = _sandbox(reservation_context)
    sandbox.start = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    middleware = SandboxMiddleware(
        sandbox=sandbox,
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    execute = next(tool for tool in middleware.tools if tool.name == "execute")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await execute.execute(
            "tool-provider-failure",
            _ExecuteArgs(
                command="build project",
                description="Build project",
                background=True,
            ),
        )

    task = await db_session.scalar(
        select(BackgroundTask).where(
            col(BackgroundTask.admission_id) == admission.id,
            col(BackgroundTask.tool_call_id) == "tool-provider-failure",
        )
    )
    assert task is not None
    command = await db_session.scalar(
        select(SandboxCommand).where(col(SandboxCommand.task_id) == task.id)
    )
    assert command is not None
    assert task.state == "unknown"
    assert task.backgrounded_at is None
    assert task.owner_token is None
    assert command.status == "starting"
    assert command.start_requested_at is not None


async def test_start_receipt_failure_stays_recoverable(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    sandbox = _sandbox(reservation_context)

    async def _fail_receipt(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(BackgroundTaskService, "register_start_receipt", _fail_receipt)
    middleware = SandboxMiddleware(
        sandbox=sandbox,
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    execute = next(tool for tool in middleware.tools if tool.name == "execute")

    result = await execute.execute(
        "tool-receipt-failure",
        _ExecuteArgs(
            command="build project",
            description="Build project",
            background=True,
        ),
    )

    assert result.is_error is True
    sandbox.kill.assert_awaited_once()
    task = await db_session.scalar(
        select(BackgroundTask).where(
            col(BackgroundTask.admission_id) == admission.id,
            col(BackgroundTask.tool_call_id) == "tool-receipt-failure",
        )
    )
    assert task is not None
    command = await db_session.scalar(
        select(SandboxCommand).where(col(SandboxCommand.task_id) == task.id)
    )
    assert command is not None
    assert task.state == "unknown"
    assert task.owner_token is None
    assert command.status == "starting"
    assert command.provider_ref is None


async def test_deadline_records_reason_before_terminal_observation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    middleware = SandboxMiddleware(
        sandbox=_sandbox(reservation_context),
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    reserved = await middleware._persist_reserve(
        command_id="unused-proposed-id",
        tool_call_id="tool-deadline",
        command="build project",
        description="Build project",
        notify_on_complete=True,
        log_path="/workspace/.cubeplex/deadline.log",
        timeout_seconds=1,
    )
    assert isinstance(reserved, _ReservedCommand)

    await middleware._persist_timed_out(reserved.command_id)

    task = await db_session.get(BackgroundTask, reserved.task_id)
    command = await db_session.get(SandboxCommand, reserved.command_id)
    assert task is not None and command is not None
    await db_session.refresh(task)
    await db_session.refresh(command)
    assert task.state == "cancelled"
    assert task.stop_reason == "deadline"
    assert task.owner_token is None
    assert command.status == "killed"


async def test_stop_winning_handoff_releases_owner_for_cleanup(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    middleware = SandboxMiddleware(
        sandbox=_sandbox(reservation_context),
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    reserved = await middleware._persist_reserve(
        command_id="unused-proposed-id",
        tool_call_id="tool-stop-before-handoff",
        command="build project",
        description="Build project",
        notify_on_complete=True,
        log_path="/workspace/.cubeplex/stopped.log",
        timeout_seconds=3600,
    )
    assert isinstance(reserved, _ReservedCommand)
    await middleware._persist_running(reserved.command_id, "provider-process")
    middleware._live_commands[reserved.command_id] = (
        ProcessHandle(command_id=reserved.command_id, provider_ref="provider-process"),
        True,
    )
    now = datetime.now(UTC)
    await ConversationExecutionService(
        db_session,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
    ).stop_run(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=admission.actor_user_id,
        run_id=admission.run_id,
        now=now,
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="stopped foreground work"):
        await middleware._handoff_conversation_command(reserved.command_id)

    task = await db_session.get(BackgroundTask, reserved.task_id)
    assert task is not None
    await db_session.refresh(task)
    assert task.backgrounded_at is None
    assert task.stop_requested_at == now
    assert task.owner_token is None
    assert task.owner_until is not None and task.owner_until <= datetime.now(UTC)
    assert reserved.command_id not in middleware._live_commands


async def test_stranded_run_recovery_stops_task_without_legacy_command_write(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    middleware = SandboxMiddleware(
        sandbox=_sandbox(reservation_context),
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    reserved = await middleware._persist_reserve(
        command_id="unused-proposed-id",
        tool_call_id="tool-stranded-recovery",
        command="build project",
        description="Build project",
        notify_on_complete=True,
        log_path="/workspace/.cubeplex/stranded.log",
        timeout_seconds=3600,
    )
    assert isinstance(reserved, _ReservedCommand)
    await middleware._persist_running(reserved.command_id, "provider-process")
    admission_id = admission.id
    run_id = admission.run_id

    await _kill_stranded_commands([run_id])

    db_session.expire_all()
    task = await db_session.get(BackgroundTask, reserved.task_id)
    command = await db_session.get(SandboxCommand, reserved.command_id)
    recovered_admission = await db_session.get(ConversationExecutionAdmission, admission_id)
    assert task is not None and command is not None and recovered_admission is not None
    assert recovered_admission.run_stop_requested_at is not None
    assert task.stop_requested_at is not None
    assert task.stop_reason == "run_stop"
    assert command.status == "starting"
    assert command.provider_ref == "provider-process"

    async def _no_sandbox(_row: SandboxCommand) -> None:
        return None

    assert (
        await kill_sandbox_commands(
            db_session,
            reservation_context.details.user_sandbox_id,
            get_sandbox=_no_sandbox,
        )
        == []
    )
    await db_session.refresh(command)
    assert command.status == "starting"
    assert command.provider_ref == "provider-process"


async def test_monitor_is_one_notifying_task_with_optional_deadline(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission,
        reservation_context.admission_id,
    )
    assert admission is not None and admission.run_id is not None
    middleware = SandboxMiddleware(
        sandbox=_sandbox(reservation_context),
        conversation_id=reservation_context.conversation_id,
        workspace_id=DEFAULT_WS_ID,
        org_id=DEFAULT_ORG_ID,
        user_id=admission.actor_user_id,
        run_id=admission.run_id,
        admission_id=admission.id,
        owner_token="foreground-owner",
        session_factory=session_factory,
    )
    monitor = next(tool for tool in middleware.tools if tool.name == "monitor")

    result = await monitor.execute(
        "tool-monitor",
        _MonitorArgs(
            command="./wait-until-ready.sh",
            description="Wait for readiness",
            persistent=True,
        ),
    )

    assert isinstance(result.details, dict)
    assert result.details["deadline_at"] is None
    assert result.details["notification"] == "once"
    task = await db_session.get(BackgroundTask, result.details["task_id"])
    command = await db_session.get(SandboxCommand, result.details["command_id"])
    assert task is not None and command is not None
    assert task.notify_on_complete is True
    assert task.deadline_at is None
    assert task.backgrounded_at is not None
    assert command.kind == "monitor"
    assert middleware._live_commands == {}
