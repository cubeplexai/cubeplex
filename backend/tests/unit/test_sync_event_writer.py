"""Unit tests for UserSandboxSyncEventService.record.

Uses an in-memory SQLite session to verify the writer creates rows and
updates the UserSandbox snapshot only on success.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

# Import to register all models on SQLModel.metadata BEFORE create_all.
from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.models.public_id import generate_public_id
from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.sandbox.sync_result import SyncResult


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_sandbox(
    factory: async_sessionmaker[AsyncSession], *, org_id: str, workspace_id: str
) -> str:
    sandbox_row_id = generate_public_id("sbx")
    async with factory() as s:
        sb = UserSandbox(
            id=sandbox_row_id,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id="user-x",
            scope_type="user",
            scope_id="user-x",
            sandbox_id="sb-test-placeholder",
            status="running",
            image="img",
        )
        s.add(sb)
        await s.commit()
    return sandbox_row_id


@pytest.mark.asyncio
async def test_record_success_inserts_event_and_updates_snapshot(session_factory):
    sandbox_row_id = await _seed_sandbox(session_factory, org_id="org-1", workspace_id="ws-1")
    svc = UserSandboxSyncEventService(session_factory)

    now = datetime.now(UTC)
    manifest: dict[str, Any] = {"schema_version": 1, "skills": {"docx": {"version": "1.0.0"}}}
    result = SyncResult(
        started_at=now,
        finished_at=now,
        status="success",
        n_pushed=1,
        n_removed=0,
        tar_size_bytes=1024,
        manifest=manifest,
        manifest_hash="sha256:abc",
        skills_count=1,
    )
    await svc.record(
        user_sandbox_id=sandbox_row_id,
        org_id="org-1",
        workspace_id="ws-1",
        result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(select(UserSandboxSyncEvent))).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.status == "success"
        assert e.n_pushed == 1
        assert e.manifest_snapshot == manifest
        sb = (
            await s.execute(select(UserSandbox).where(UserSandbox.id == sandbox_row_id))
        ).scalar_one()
        assert sb.skills_manifest_hash == "sha256:abc"
        assert sb.skills_count == 1
        assert sb.last_skill_sync_at is not None
        assert sb.last_skill_sync_event_id == e.id


@pytest.mark.asyncio
async def test_record_failed_inserts_event_but_not_snapshot(session_factory):
    sandbox_row_id = await _seed_sandbox(session_factory, org_id="org-1", workspace_id="ws-1")
    svc = UserSandboxSyncEventService(session_factory)

    now = datetime.now(UTC)
    result = SyncResult(
        started_at=now,
        finished_at=now,
        status="failed",
        error_type="SandboxError",
        error_message="extract failed",
    )
    await svc.record(
        user_sandbox_id=sandbox_row_id,
        org_id="org-1",
        workspace_id="ws-1",
        result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(select(UserSandboxSyncEvent))).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.status == "failed"
        assert e.manifest_snapshot is None
        assert e.error_type == "SandboxError"
        assert e.error_message == "extract failed"
        sb = (
            await s.execute(select(UserSandbox).where(UserSandbox.id == sandbox_row_id))
        ).scalar_one()
        assert sb.skills_manifest_hash is None
        assert sb.last_skill_sync_at is None
        assert sb.last_skill_sync_event_id is None
