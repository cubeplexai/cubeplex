"""Unit tests for UserSandboxRepository: polymorphic scope + active-state
hardening."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.repositories.user_sandbox import UserSandboxRepository


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
    rec = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    assert rec.status == "provisioning"
    active = await repo.get_active_by_scope(scope_type="user", scope_id="user-1")
    assert active is not None and active.id == rec.id


async def test_second_reserve_for_same_identity_raises(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    with pytest.raises(IntegrityError):
        await repo.reserve(
            user_id="user-1",
            image="ubuntu:22.04",
            ttl_seconds=600,
            scope_type="user",
            scope_id="user-1",
        )


async def test_promote_sets_running_and_sandbox_id(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    await repo.promote_to_running(rec.id, sandbox_id="prov-abc")
    refreshed = await repo.get(rec.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.sandbox_id == "prov-abc"


async def test_delete_record_frees_the_slot(session: AsyncSession) -> None:
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    await repo.delete_record(rec.id)
    # Slot free again — a fresh reserve must succeed.
    again = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    assert again.status == "provisioning"


# ── Polymorphic scope ────────────────────────────────────────────────


async def test_personal_and_topic_sandbox_can_coexist(session: AsyncSession) -> None:
    """A personal (scope_type='user') and a dedicated topic sandbox for the
    same (org, ws, user) can both be running simultaneously."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    personal = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    topic = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="topic",
        scope_id="top-aaa",
    )
    await repo.promote_to_running(personal.id, sandbox_id="prov-p")
    await repo.promote_to_running(topic.id, sandbox_id="prov-t")

    by_user = await repo.get_active_by_scope(scope_type="user", scope_id="user-1")
    by_topic = await repo.get_active_by_scope(scope_type="topic", scope_id="top-aaa")
    assert by_user is not None and by_user.id == personal.id
    assert by_topic is not None and by_topic.id == topic.id


async def test_conversation_scope_distinct_from_topic_and_user(session: AsyncSession) -> None:
    """The 'conversation' scope is a separate key from 'user' and 'topic';
    the same id collides across types only if the type matches."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    conv = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="conversation",
        scope_id="conv-aaa",
    )
    await repo.promote_to_running(conv.id, sandbox_id="prov-c")

    found = await repo.get_active_by_scope(scope_type="conversation", scope_id="conv-aaa")
    assert found is not None and found.id == conv.id
    # Lookups under a different scope_type must not see the row.
    assert await repo.get_active_by_scope(scope_type="topic", scope_id="conv-aaa") is None
    assert await repo.get_active_by_scope(scope_type="user", scope_id="conv-aaa") is None


async def test_second_reserve_for_same_topic_raises(session: AsyncSession) -> None:
    """``uq_user_sandbox_active_scope`` blocks a second concurrent
    reserve on the same scope key regardless of user_id."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="topic",
        scope_id="top-aaa",
    )
    with pytest.raises(IntegrityError):
        await repo.reserve(
            user_id="user-2",
            image="ubuntu:22.04",
            ttl_seconds=600,
            scope_type="topic",
            scope_id="top-aaa",
        )


async def test_race_loss_recovery_finds_winner_via_scope(session: AsyncSession) -> None:
    """When two participants race ``reserve`` on the same scope key, the
    loser must still find the winner via ``get_active_by_scope`` even
    though the winner row's ``user_id`` belongs to the other participant."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    winner = await repo.reserve(
        user_id="alice",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="topic",
        scope_id="top-aaa",
    )
    with pytest.raises(IntegrityError):
        await repo.reserve(
            user_id="bob",
            image="ubuntu:22.04",
            ttl_seconds=600,
            scope_type="topic",
            scope_id="top-aaa",
        )
    await session.rollback()

    # Loser (bob) attaches to the topic winner — keyed by topic, not by user.
    found = await repo.get_active_by_scope(scope_type="topic", scope_id="top-aaa")
    assert found is not None
    assert found.id == winner.id
    assert found.user_id == "alice"


async def test_rekey_moves_active_row_across_scope_keys(session: AsyncSession) -> None:
    """``rekey`` re-scopes the active sandbox row in place — used by the
    upgrade endpoints to inherit a running sandbox under the new scope."""
    repo = UserSandboxRepository(session, org_id="org-1", workspace_id="ws-1")
    rec = await repo.reserve(
        user_id="user-1",
        image="ubuntu:22.04",
        ttl_seconds=600,
        scope_type="user",
        scope_id="user-1",
    )
    await repo.promote_to_running(rec.id, sandbox_id="prov-uc")

    await repo.rekey(
        from_scope_type="user",
        from_scope_id="user-1",
        to_scope_type="conversation",
        to_scope_id="conv-xyz",
    )
    await session.commit()

    # Old key has nothing; new key points at the same row.
    assert await repo.get_active_by_scope(scope_type="user", scope_id="user-1") is None
    moved = await repo.get_active_by_scope(scope_type="conversation", scope_id="conv-xyz")
    assert moved is not None and moved.id == rec.id
    assert moved.sandbox_id == "prov-uc"
