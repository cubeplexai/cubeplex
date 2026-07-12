"""Tests for ScheduledTaskService (needs real DB).

Uses the standard SQLAlchemy "join session into external transaction"
pattern: the fixture opens a real connection + transaction, binds the
session to it, and monkeypatches ``session.commit`` → ``session.flush``
so the service's commit calls are harmless. After the test the outer
transaction rolls back, leaving a clean DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
)
from cubeplex.db.engine import _build_database_url
from cubeplex.models.membership import Role
from cubeplex.models.organization import Organization
from cubeplex.models.user import User
from cubeplex.models.workspace import Workspace
from cubeplex.services.scheduled_task import ScheduledTaskService

pytestmark = pytest.mark.e2e

_ORG_ID = "org-test0000000000"
_WS_ID = "ws-test00000000000"
_OWNER_ID = "usr-owner00000000"
_ADMIN_ID = "usr-admin00000000"
_OTHER_ID = "usr-other00000000"


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    """Transactional session: everything rolls back after each test."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    conn = await engine.connect()
    txn = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)

    # Redirect session.commit → flush so the service's commits are
    # harmless but the data is visible within the transaction.
    _real_commit = session.commit
    session.commit = session.flush  # type: ignore[assignment]

    # For _resume_next_fire's begin_nested (SAVEPOINT), we need the
    # real connection-level begin_nested. Patch session.begin_nested
    # to use the connection's begin_nested which works inside the
    # outer transaction.
    # (This works because session is already bound to conn.)

    try:
        # Seed FK parent rows
        session.add(Organization(id=_ORG_ID, name="test", slug="svc-test"))
        await session.flush()
        session.add(Workspace(id=_WS_ID, org_id=_ORG_ID, name="test ws"))
        await session.flush()
        for uid, email in [
            (_OWNER_ID, "owner@test.local"),
            (_ADMIN_ID, "admin@test.local"),
            (_OTHER_ID, "other@test.local"),
        ]:
            session.add(User(id=uid, email=email, hashed_password="x"))
        await session.flush()

        yield session
    finally:
        await session.close()
        await txn.rollback()
        await conn.close()
        await engine.dispose()


def _owner_ctx() -> ScopeContext:
    return ScopeContext(
        org_id=_ORG_ID,
        workspace_id=_WS_ID,
        user_id=_OWNER_ID,
        role=Role.MEMBER,
    )


def _admin_ctx() -> ScopeContext:
    return ScopeContext(
        org_id=_ORG_ID,
        workspace_id=_WS_ID,
        user_id=_ADMIN_ID,
        role=Role.ADMIN,
    )


def _other_ctx() -> ScopeContext:
    return ScopeContext(
        org_id=_ORG_ID,
        workspace_id=_WS_ID,
        user_id=_OTHER_ID,
        role=Role.MEMBER,
    )


def _interval_data(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "test task",
        "prompt": "do something",
        "schedule_kind": "interval",
        "interval_seconds": 3600,
        "timezone": "UTC",
        "target_mode": "new_each_run",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# TestCreate
# ------------------------------------------------------------------


class TestCreate:
    async def test_create_interval_task(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        ctx = _owner_ctx()
        task = await svc.create(ctx, db_session, _interval_data())
        assert task.status == "active"
        assert task.next_fire_at is not None
        assert task.owner_user_id == ctx.user_id

    async def test_cron_requires_expr(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        with pytest.raises(ActionInvalidInput, match="cron_expr"):
            await svc.create(
                _owner_ctx(),
                db_session,
                {
                    "name": "bad",
                    "prompt": "x",
                    "schedule_kind": "cron",
                    "target_mode": "new_each_run",
                },
            )

    async def test_once_requires_run_at(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        with pytest.raises(ActionInvalidInput, match="run_at"):
            await svc.create(
                _owner_ctx(),
                db_session,
                {
                    "name": "bad",
                    "prompt": "x",
                    "schedule_kind": "once",
                    "target_mode": "new_each_run",
                },
            )


# ------------------------------------------------------------------
# TestAuthorization
# ------------------------------------------------------------------


class TestAuthorization:
    async def test_owner_can_pause(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        task = await svc.create(_owner_ctx(), db_session, _interval_data())
        paused = await svc.pause(_owner_ctx(), db_session, task.id)
        assert paused.status == "paused"

    async def test_admin_can_pause(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        task = await svc.create(_owner_ctx(), db_session, _interval_data())
        paused = await svc.pause(_admin_ctx(), db_session, task.id)
        assert paused.status == "paused"

    async def test_other_member_cannot_pause(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        task = await svc.create(_owner_ctx(), db_session, _interval_data())
        with pytest.raises(ActionPermissionDenied):
            await svc.pause(_other_ctx(), db_session, task.id)


# ------------------------------------------------------------------
# TestPauseResume
# ------------------------------------------------------------------


class TestPauseResume:
    async def test_pause_resume_cycle(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        ctx = _owner_ctx()
        task = await svc.create(ctx, db_session, _interval_data())
        assert task.status == "active"

        paused = await svc.pause(ctx, db_session, task.id)
        assert paused.status == "paused"

        resumed = await svc.resume(ctx, db_session, task.id)
        assert resumed.status == "active"
        assert resumed.next_fire_at is not None


# ------------------------------------------------------------------
# TestDelete
# ------------------------------------------------------------------


class TestDelete:
    async def test_delete_then_get_raises(self, db_session):  # type: ignore[no-untyped-def]
        svc = ScheduledTaskService()
        ctx = _owner_ctx()
        task = await svc.create(ctx, db_session, _interval_data())
        await svc.delete(ctx, db_session, task.id)
        with pytest.raises(ActionNotFound):
            await svc.get_task(ctx, db_session, task.id)


# ------------------------------------------------------------------
# TestUpdate
# ------------------------------------------------------------------


class TestUpdate:
    async def test_prompt_update_keeps_next_fire(
        self,
        db_session,  # type: ignore[no-untyped-def]
    ):
        svc = ScheduledTaskService()
        ctx = _owner_ctx()
        task = await svc.create(ctx, db_session, _interval_data())
        original_next = task.next_fire_at

        updated = await svc.update(ctx, db_session, task.id, {"prompt": "new prompt"})
        assert updated.prompt == "new prompt"
        assert updated.next_fire_at == original_next

    async def test_interval_update_recomputes_next_fire(
        self,
        db_session,  # type: ignore[no-untyped-def]
    ):
        svc = ScheduledTaskService()
        ctx = _owner_ctx()
        task = await svc.create(ctx, db_session, _interval_data())
        original_next = task.next_fire_at

        updated = await svc.update(ctx, db_session, task.id, {"interval_seconds": 7200})
        # Schedule field changed -> next_fire_at recomputed
        assert updated.next_fire_at != original_next
