"""Coordinator marks expired inflight rows from LocalSandbox polls."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import cubeplex.models  # noqa: F401  — register metadata
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandStatus
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.base import ProcessHandle
from cubeplex.sandbox.command_coordinator import kill_run_commands, reconcile_once
from cubeplex.sandbox.local import LocalSandbox


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
) -> SandboxCommand:
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
    assert row.notice_state == "none"


@pytest.mark.asyncio
async def test_kill_run_commands_stops_process(session: AsyncSession) -> None:
    sandbox = LocalSandbox()
    row = await _row(session, sandbox=sandbox, command="sleep 30")

    async def _get(_row: SandboxCommand) -> LocalSandbox:
        del _row
        return sandbox

    finished = await kill_run_commands(session, "run-1", get_sandbox=_get)
    assert row.id in finished
    await session.refresh(row)
    assert row.status == SandboxCommandStatus.killed.value
    assert row.provider_ref is not None
    snap = await sandbox.poll(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))
    assert snap.status == "killed"


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
