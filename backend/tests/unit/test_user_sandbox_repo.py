"""Unit tests for UserSandboxRepository active-state hardening (Task 5) +
topic-keyed scoping (Task 2.5)."""

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


# ── Task 2.5: topic-keyed scoping ────────────────────────────────────


async def test_personal_and_topic_sandbox_can_coexist(session: AsyncSession) -> None:
    """A personal (topic_id=NULL) and a dedicated topic sandbox for the
    same (org, ws, user) can both be running simultaneously."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    personal = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    topic = await repo.reserve(
        user_id="user-1", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa"
    )
    await repo.promote_to_running(personal.id, sandbox_id="prov-p")
    await repo.promote_to_running(topic.id, sandbox_id="prov-t")

    by_user = await repo.get_active_by_user("user-1")
    by_topic = await repo.get_active_by_topic("top-aaa")
    assert by_user is not None and by_user.id == personal.id
    assert by_topic is not None and by_topic.id == topic.id


async def test_get_active_for_scope_dispatches_by_topic_id(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    personal = await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600)
    topic = await repo.reserve(
        user_id="user-1", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa"
    )
    await repo.promote_to_running(personal.id, sandbox_id="prov-p")
    await repo.promote_to_running(topic.id, sandbox_id="prov-t")

    via_personal = await repo.get_active_for_scope(user_id="user-1", topic_id=None)
    via_topic = await repo.get_active_for_scope(user_id="user-1", topic_id="top-aaa")
    assert via_personal is not None and via_personal.id == personal.id
    assert via_topic is not None and via_topic.id == topic.id


async def test_second_reserve_for_same_topic_raises(session: AsyncSession) -> None:
    """``uq_user_sandbox_active_topic`` blocks a second concurrent
    reserve on the same topic regardless of user_id."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    await repo.reserve(user_id="user-1", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa")
    with pytest.raises(IntegrityError):
        await repo.reserve(
            user_id="user-2", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa"
        )


async def test_race_loss_recovery_finds_winner_via_scope(session: AsyncSession) -> None:
    """When two participants race ``reserve(topic_id=T)``, the loser must
    still find the winner via ``get_active_for_scope`` even though the
    winner row's ``user_id`` belongs to the other participant."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    winner = await repo.reserve(
        user_id="alice", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa"
    )
    with pytest.raises(IntegrityError):
        await repo.reserve(user_id="bob", image="ubuntu:22.04", ttl_seconds=600, topic_id="top-aaa")
    await session.rollback()  # drop the failed insert's snapshot before re-reading

    # Loser (bob) attaches to the topic winner — keyed by topic, not by user.
    found = await repo.get_active_for_scope(user_id="bob", topic_id="top-aaa")
    assert found is not None
    assert found.id == winner.id
    assert found.user_id == "alice"  # winner row owns the topic; user_id differs
