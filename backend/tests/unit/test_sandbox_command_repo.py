"""In-process sqlite tests for sandbox_commands reserve/cap."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import cubeplex.models  # noqa: F401  — register metadata
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.sandbox_command import (
    MAX_INFLIGHT_COMMANDS,
    SandboxCommandCapError,
    SandboxCommandRepository,
)
from cubeplex.repositories.user_sandbox import UserSandboxRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _sandbox(session: AsyncSession) -> UserSandbox:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    return await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )


def _until() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=15)


async def test_reserve_inserts_starting_row(session: AsyncSession) -> None:
    us = await _sandbox(session)
    repo = SandboxCommandRepository(session, org_id="org-1", workspace_id="ws-1")
    row = await repo.reserve(
        user_sandbox_id=us.id,
        conversation_id="conv-1",
        run_id="run-1",
        tool_call_id="tc-1",
        started_by_user_id="user-1",
        command="sleep 1",
        description="sleep",
        notify_on_complete=True,
        owner_id="run:run-1",
        owner_until=_until(),
        log_path="/workspace/.cubeplex/execute-x.log",
        command_id="scmd-testreserve01",
    )
    assert row.id == "scmd-testreserve01"
    assert row.status == "starting"
    inflight = await repo.list_inflight_for_run("run-1")
    assert [r.id for r in inflight] == ["scmd-testreserve01"]


async def test_discard_reservation_removes_short_foreground_row(
    session: AsyncSession,
) -> None:
    us = await _sandbox(session)
    repo = SandboxCommandRepository(session, org_id="org-1", workspace_id="ws-1")
    row = await repo.reserve(
        user_sandbox_id=us.id,
        conversation_id="conv-1",
        run_id="run-1",
        tool_call_id="tc-1",
        started_by_user_id="user-1",
        command="echo quick",
        description="quick",
        notify_on_complete=True,
        owner_id="run:run-1",
        owner_until=_until(),
        log_path="/tmp/quick.log",
    )

    assert await repo.discard_reservation(row.id, owner_id="run:run-1") is True
    assert await repo.get(row.id) is None


async def test_log_cursor_update_requires_current_owner(session: AsyncSession) -> None:
    us = await _sandbox(session)
    repo = SandboxCommandRepository(session, org_id="org-1", workspace_id="ws-1")
    row = await repo.reserve(
        user_sandbox_id=us.id,
        conversation_id="conv-1",
        run_id="run-1",
        tool_call_id="tc-cursor",
        started_by_user_id="user-1",
        command="long command",
        description="long command",
        notify_on_complete=True,
        owner_id="run:run-1",
        owner_until=_until(),
        log_path="/tmp/cursor.log",
    )
    assert await repo.mark_running(
        row.id,
        provider_ref="provider-1",
        owner_id="run:run-1",
    )

    assert not await repo.update_log_cursor(
        row.id,
        log_cursor="16",
        owner_id="run:other",
    )
    assert await repo.update_log_cursor(
        row.id,
        log_cursor="17",
        owner_id="run:run-1",
    )
    await session.refresh(row)
    assert row.log_cursor == "17"


async def test_reserve_rejects_ninth_inflight(session: AsyncSession) -> None:
    us = await _sandbox(session)
    repo = SandboxCommandRepository(session, org_id="org-1", workspace_id="ws-1")
    for i in range(MAX_INFLIGHT_COMMANDS):
        await repo.reserve(
            user_sandbox_id=us.id,
            conversation_id="conv-1",
            run_id="run-1",
            tool_call_id=f"tc-{i}",
            started_by_user_id="user-1",
            command="sleep 1",
            description="sleep",
            notify_on_complete=True,
            owner_id="run:run-1",
            owner_until=_until(),
            log_path=f"/tmp/{i}.log",
        )
    with pytest.raises(SandboxCommandCapError):
        await repo.reserve(
            user_sandbox_id=us.id,
            conversation_id="conv-1",
            run_id="run-1",
            tool_call_id="tc-overflow",
            started_by_user_id="user-1",
            command="sleep 1",
            description="sleep",
            notify_on_complete=True,
            owner_id="run:run-1",
            owner_until=_until(),
            log_path="/tmp/overflow.log",
        )
