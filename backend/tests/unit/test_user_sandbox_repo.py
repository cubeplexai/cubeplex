"""Unit tests for UserSandboxRepository active-state hardening (Task 5)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.repositories.user_sandbox import UserSandboxRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # SQLite has FK enforcement off by default, so parent-table rows are not
    # required for these tests; the repo only filters by scope columns.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_reserve_then_active_query_includes_provisioning(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    assert rec.status == "provisioning"
    active = await repo.get_active_by_user("user-1")
    assert active is not None and active.id == rec.id


async def test_second_reserve_for_same_identity_raises(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    with pytest.raises(IntegrityError):
        await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)


async def test_promote_sets_running_and_sandbox_id(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    await repo.promote_to_running(rec.id, sandbox_id="prov-abc")
    refreshed = await repo.get(rec.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.sandbox_id == "prov-abc"


async def test_delete_record_frees_the_slot(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    await repo.delete_record(rec.id)
    # Slot free again — a fresh reserve must succeed.
    again = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    assert again.status == "provisioning"
