"""Coordinator marks expired inflight rows from LocalSandbox polls."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, col

import cubeplex.models  # noqa: F401  — register metadata
from cubeplex.models import Conversation
from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandKind,
    SandboxCommandLifetime,
    SandboxCommandNoticeState,
    SandboxCommandStatus,
    SandboxCommandWake,
)
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot
from cubeplex.sandbox.command_coordinator import (
    COMMAND_POLL_INTERVAL_SECONDS,
    MAX_LINE_WAKES,
    _append_log,
    _renew_conversation_row,
    _wake_steer_id,
    enqueue_pending_notices_once,
    kill_command,
    kill_run_commands,
    kill_sandbox_commands,
    reconcile_once,
    sandbox_from_row,
)
from cubeplex.sandbox.local import LocalSandbox


def test_wake_steer_id_is_stable_per_run_and_changes_between_runs() -> None:
    first = _wake_steer_id("scmw-123", "run-1")

    assert first == _wake_steer_id("scmw-123", "run-1")
    assert first != _wake_steer_id("scmw-123", "run-2")
    assert first.startswith("scmw-123:")
    assert len(first) <= 64


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _row(
    session: AsyncSession,
    *,
    sandbox: LocalSandbox,
    command: str,
    run_id: str = "run-1",
    kind: str = SandboxCommandKind.execute.value,
    lifetime: str = SandboxCommandLifetime.run.value,
) -> SandboxCommand:
    conversation = await session.get(Conversation, "conv-1")
    if conversation is None:
        conversation = Conversation(
            id="conv-1",
            org_id="org-1",
            workspace_id="ws-1",
            creator_user_id="user-1",
            title="command coordinator test",
        )
        session.add(conversation)
        await session.flush()
    us_repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    us = await us_repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    handle = await sandbox.start(command)
    row = SandboxCommand(
        user_sandbox_id=us.id,
        conversation_id="conv-1",
        run_id=run_id,
        tool_call_id="tc-1",
        started_by_user_id="user-1",
        command=command,
        provider="local",
        provider_ref=handle.provider_ref,
        status=SandboxCommandStatus.running.value,
        notify_on_complete=True,
        log_path="",
        owner_id="dead-worker",
        owner_until=datetime.now(UTC) - timedelta(seconds=30),
        kind=kind,
        lifetime=lifetime,
    )
    row.org_id = "org-1"
    row.workspace_id = "ws-1"
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_reconcile_marks_exited_command(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="true")
    await asyncio.sleep(0.05)

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await reconcile_once(session, get_sandbox=_get)
    assert row.id in finished
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.exited.value
    assert row.notice_state == SandboxCommandNoticeState.pending.value


@pytest.mark.asyncio
async def test_conversation_execute_deadline_is_enforced(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    row.monitor_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(row)
    await session.commit()

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await reconcile_once(session, get_sandbox=_get)

    assert finished == [row.id]
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value


@pytest.mark.asyncio
async def test_pending_execute_notice_is_moved_to_wake_outbox(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import run_events

    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="true")
    await asyncio.sleep(0.05)
    row.status = SandboxCommandStatus.exited.value
    row.notice_state = SandboxCommandNoticeState.pending.value
    row.exit_code = 0
    session.add(row)
    await session.commit()

    async def _no_active(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(run_events, "get_active_run", _no_active)
    assert session.bind is not None
    maker = async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)

    wake_ids = await enqueue_pending_notices_once(
        maker,
        redis=object(),  # type: ignore[arg-type]
        redis_key_prefix="test",
    )

    assert len(wake_ids) == 1
    wake = await session.get(SandboxCommandWake, wake_ids[0])
    assert wake is not None
    assert wake.command_id == row.id
    assert wake.reason == "completion"


@pytest.mark.asyncio
async def test_log_append_does_not_put_large_output_in_shell_command(tmp_path: Path) -> None:
    path = tmp_path / ".cubeplex" / "execute-large.log"
    sandbox = LocalSandbox(workdir=str(tmp_path))
    output = "x" * 1_000_000

    await _append_log(sandbox, str(path), output)

    assert path.read_text() == output
    assert list(path.parent.glob("*.append-*")) == []


@pytest.mark.asyncio
async def test_reconcile_kills_conversation_command_after_conversation_deleted(
    session: AsyncSession,
) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    conversation = await session.get(Conversation, row.conversation_id)
    assert conversation is not None
    conversation.deleted_at = datetime.now(UTC)
    session.add(conversation)
    await session.commit()

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await reconcile_once(session, get_sandbox=_get)
    assert finished == [row.id]
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value
    assert row.provider_ref is None


@pytest.mark.asyncio
async def test_kill_run_commands_stops_process(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="sleep 30")
    provider_ref = row.provider_ref
    assert provider_ref is not None

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await kill_run_commands(session, "run-1", get_sandbox=_get)
    assert row.id in finished
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value
    assert row.provider_ref is None
    snap = await sandbox.poll(ProcessHandle(command_id=row.id, provider_ref=provider_ref))
    assert snap.status == "killed"


@pytest.mark.asyncio
async def test_kill_command_does_not_terminalize_when_sandbox_reconnect_fails(
    session: AsyncSession,
) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="sleep 30")

    async def _missing(_row: SandboxCommand) -> None:
        del _row
        return None

    killed = await kill_command(
        session,
        row,
        get_sandbox=_missing,  # type: ignore[arg-type]
    )
    assert killed is False
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.running.value
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))


@pytest.mark.asyncio
async def test_kill_command_persists_final_output_cursor_and_monitor_wake(
    session: AsyncSession,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from cubeplex.sandbox.base import ExecuteResult

    seed_sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=seed_sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    provider_ref = row.provider_ref
    assert provider_ref is not None
    row.log_cursor = "4"
    row.log_path = "/work/.cubeplex/execute-monitor.log"
    session.add(row)
    await session.commit()
    sandbox = MagicMock()
    sandbox.workdir = "/work"
    sandbox.kill = AsyncMock()
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(
            status="killed",
            new_output="final buffered line\n",
            log_cursor="9",
        )
    )
    sandbox.acknowledge_output = AsyncMock()
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))

    async def _get(_row: SandboxCommand) -> object:
        return sandbox

    killed = await kill_command(
        session,
        row,
        get_sandbox=_get,  # type: ignore[arg-type]
    )

    assert killed is True
    handle = sandbox.kill.await_args.args[0]
    assert handle.log_cursor == "4"
    await session.refresh(row)
    assert row.log_cursor == "9"
    sandbox.acknowledge_output.assert_awaited_once_with(handle, "9")
    uploaded = sandbox.upload.await_args.args[0]
    assert uploaded[0][1] == b"final buffered line\n"
    wake = (
        await session.execute(
            select(SandboxCommandWake).where(col(SandboxCommandWake.command_id) == row.id)
        )
    ).scalar_one()
    assert wake.text_tail == "final buffered line\n"
    await seed_sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=provider_ref))


@pytest.mark.asyncio
async def test_sandbox_revival_terminalizes_old_process_handle(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.models import UserSandbox
    from cubeplex.sandbox import manager as manager_module

    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    local_ref = row.provider_ref
    assert local_ref is not None
    sandbox_row = await session.get(UserSandbox, row.user_sandbox_id)
    assert sandbox_row is not None
    sandbox_row.status = "running"
    sandbox_row.sandbox_id = "provider-old"
    session.add(sandbox_row)
    await session.commit()

    replacement = SimpleNamespace(id="provider-new")
    manager = SimpleNamespace(
        get_or_create=AsyncMock(return_value=SimpleNamespace(sandbox=replacement)),
        touch=AsyncMock(),
    )
    monkeypatch.setattr(manager_module, "get_sandbox_manager", lambda: manager)

    attached = await sandbox_from_row(row, session)

    await session.refresh(row)
    assert attached is None
    assert row.status == SandboxCommandStatus.killed.value
    assert row.provider_ref is None
    manager.touch.assert_not_awaited()
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=local_ref))


@pytest.mark.asyncio
async def test_sandbox_kill_terminalizes_monitor_and_enqueues_exit_wake(
    session: AsyncSession,
) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    provider_ref = row.provider_ref
    assert provider_ref is not None

    async def _already_destroyed(_row: SandboxCommand) -> None:
        del _row
        return None

    finished = await kill_sandbox_commands(
        session,
        row.user_sandbox_id,
        get_sandbox=_already_destroyed,  # type: ignore[arg-type]
    )
    assert finished == [row.id]
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value
    wake = (
        await session.execute(
            select(SandboxCommandWake).where(col(SandboxCommandWake.command_id) == row.id)
        )
    ).scalar_one()
    assert wake.reason == "exit"
    assert row.provider_ref is None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=provider_ref))


@pytest.mark.asyncio
async def test_reconcile_kills_abandoned_running_command(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="sleep 30")

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await reconcile_once(session, get_sandbox=_get)
    assert row.id in finished
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value


@pytest.mark.asyncio
async def test_reconcile_does_not_terminalize_if_interrupt_fails(
    session: AsyncSession,
) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="sleep 30")

    class _Boom:
        async def kill(self, handle: ProcessHandle) -> None:
            del handle
            raise RuntimeError("interrupt failed")

        async def poll(self, handle: ProcessHandle) -> object:
            return await sandbox.poll(handle)

    async def _get(_row: SandboxCommand) -> object:
        del _row
        return _Boom()

    finished = await reconcile_once(session, get_sandbox=_get)  # type: ignore[arg-type]
    assert finished == []
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.running.value


@pytest.mark.asyncio
async def test_reconcile_retries_when_sandbox_attachment_fails(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )

    async def _get(_row: SandboxCommand) -> None:
        del _row
        return None

    finished = await reconcile_once(session, get_sandbox=_get)

    assert finished == []
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.running.value
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))


@pytest.mark.asyncio
async def test_monitor_line_creates_one_durable_wake(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="printf 'FAILED\\n'; sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    await asyncio.sleep(0.05)

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await reconcile_once(session, get_sandbox=_get)
    assert finished == []
    wakes = list(
        (
            await session.execute(
                select(SandboxCommandWake).where(col(SandboxCommandWake.command_id) == row.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(wake.reason, wake.text_tail) for wake in wakes] == [("line", "FAILED")]
    await session.refresh(row)
    assert row.wake_count == 1
    assert row.status == SandboxCommandStatus.running.value
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))


@pytest.mark.asyncio
async def test_monitor_rate_limit_promotes_to_exit_only(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )

    class _ChattySandbox:
        def __init__(self) -> None:
            self.cursor = 0

        async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
            del handle
            self.cursor += 1
            return ProcessSnapshot(
                status="running",
                new_output=f"line-{self.cursor}\\n",
                log_cursor=str(self.cursor),
            )

        async def acknowledge_output(self, handle: ProcessHandle, cursor: str) -> None:
            handle.log_cursor = cursor

    chatty = _ChattySandbox()

    async def _get(_row: SandboxCommand) -> object:
        del _row
        return chatty

    start = datetime.now(UTC)
    for seconds in range(4):
        await reconcile_once(
            session,
            get_sandbox=_get,  # type: ignore[arg-type]
            now=start + timedelta(seconds=seconds),
        )
    await session.refresh(row)
    assert row.wake_count == 1
    assert row.wake_drops == 3
    assert row.line_wakes_disabled is True
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))


@pytest.mark.asyncio
async def test_renew_conversation_row_requires_owner_and_outlives_poll_interval(
    session: AsyncSession,
) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    now = datetime.now(UTC)

    assert not await _renew_conversation_row(
        session,
        row,
        now=now,
        owner_id="other-worker",
    )
    await session.refresh(row)
    assert row.owner_id == "dead-worker"

    assert await _renew_conversation_row(
        session,
        row,
        now=now,
        owner_id="dead-worker",
    )
    await session.refresh(row)
    assert row.owner_until is not None
    assert (
        row.owner_until.replace(tzinfo=UTC) - now
    ).total_seconds() > COMMAND_POLL_INTERVAL_SECONDS
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))


@pytest.mark.asyncio
async def test_wake_cap_does_not_kill_low_volume_monitor(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(
        session,
        sandbox=sandbox,
        command="sleep 30",
        kind=SandboxCommandKind.monitor.value,
        lifetime=SandboxCommandLifetime.conversation.value,
    )
    row.wake_count = MAX_LINE_WAKES
    row.line_wakes_disabled = True
    session.add(row)
    await session.commit()

    class _LowVolumeSandbox:
        async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
            del handle
            return ProcessSnapshot(status="running", new_output="one line\n")

    low_volume = _LowVolumeSandbox()

    async def _get(_row: SandboxCommand) -> object:
        del _row
        return low_volume

    start = datetime.now(UTC)
    for seconds in (0, 31, 62):
        assert (
            await reconcile_once(
                session,
                get_sandbox=_get,  # type: ignore[arg-type]
                now=start + timedelta(seconds=seconds),
            )
            == []
        )
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.running.value
    assert row.flood_started_at is None
    assert row.provider_ref is not None
    await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))
