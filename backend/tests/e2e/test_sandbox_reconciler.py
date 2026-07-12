"""E2E DB tests for the reconciler selection query (Task 5b / OQ-3).

Verifies ``UserSandboxRepository.list_transient_for_reconcile_system`` matches
``pausing``/``resuming`` rows whose ``last_provider_check`` is NULL or older
than ``claim_timeout`` seconds, and skips fresh ones. Also verifies that
``touch_provider_check`` stamps ``last_provider_check`` and so removes the row
from the next selection window.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def db_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def scope(
    db_session_maker: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Seed a fresh org + workspace + user, returning their ids."""
    from fastapi_users.db import SQLAlchemyUserDatabase
    from fastapi_users.schemas import BaseUserCreate

    from cubeplex.auth.users import UserManager, _slugify_org_name
    from cubeplex.models import Role, User
    from cubeplex.repositories import (
        MembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    async with db_session_maker() as session:
        org_repo = OrganizationRepository(session)
        ws_repo = WorkspaceRepository(session)
        mem_repo = MembershipRepository(session)
        email = f"sbx-reconciler-{secrets.token_hex(4)}@example.com"
        password = secrets.token_urlsafe(16)
        org_name = f"Org {email}"
        org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
        ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
        await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
        await session.commit()
        return {"org_id": org.id, "workspace_id": ws.id, "user_id": user.id}


async def _mk(
    repo: UserSandboxRepository,
    scope: dict[str, str],
    *,
    status: str,
    last_provider_check: datetime | None = None,
) -> UserSandbox:
    row = UserSandbox(
        user_id=scope["user_id"],
        sandbox_id=f"sbx_{secrets.token_hex(6)}",
        image="img:latest",
        status=status,
        ttl_seconds=3600,
        last_activity_at=datetime.now(UTC),
        last_provider_check=last_provider_check,
    )
    return await repo.add(row)


def _mk_repo(db_session: AsyncSession, scope: dict[str, str]) -> UserSandboxRepository:
    return UserSandboxRepository(
        db_session, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
    )


async def test_list_transient_includes_null_and_old_checks(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """``last_provider_check`` NULL or older than claim_timeout qualifies; rows
    in other statuses (``running``, ``paused``) and freshly-checked transients
    are excluded.
    """
    repo = _mk_repo(db_session, scope)

    now = datetime.now(UTC)
    null_pausing = await _mk(repo, scope, status="pausing", last_provider_check=None)
    null_resuming = await _mk(repo, scope, status="resuming", last_provider_check=None)
    old_pausing = await _mk(
        repo, scope, status="pausing", last_provider_check=now - timedelta(seconds=120)
    )
    # Fresh: checked just now (well within the 60s window) -> excluded.
    await _mk(repo, scope, status="pausing", last_provider_check=now)
    # Wrong status -> excluded.
    await _mk(repo, scope, status="running", last_provider_check=None)
    await _mk(repo, scope, status="paused", last_provider_check=None)

    rows = await UserSandboxRepository.list_transient_for_reconcile_system(
        db_session, claim_timeout=60
    )

    # Restrict to rows from this test's scope — list_transient is a system-scope
    # query and the test DB is shared across tests.
    scoped_ids = {r.id for r in rows if r.workspace_id == scope["workspace_id"]}
    assert scoped_ids == {null_pausing.id, null_resuming.id, old_pausing.id}


async def test_list_transient_excludes_running_with_stale_provider_check(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """A ``running`` row with a stale ``last_provider_check`` MUST NOT be
    selected — the predicate is ``status IN (pausing, resuming) AND
    (last_provider_check IS NULL OR is stale)``, so without the parentheses
    around the OR clause, AND/OR precedence would let stale-but-non-transient
    rows slip through.
    """
    repo = _mk_repo(db_session, scope)
    now = datetime.now(UTC)

    # Running row with stale check — would match if the OR predicate isn't
    # parenthesised.
    running_stale = await _mk(
        repo, scope, status="running", last_provider_check=now - timedelta(seconds=600)
    )
    # Paused row with stale check — same.
    paused_stale = await _mk(
        repo, scope, status="paused", last_provider_check=now - timedelta(seconds=600)
    )
    # A real transient row to make sure the query still returns something for
    # this scope (so the scoping assertion below doesn't trivially pass).
    pausing_null = await _mk(repo, scope, status="pausing", last_provider_check=None)

    rows = await UserSandboxRepository.list_transient_for_reconcile_system(
        db_session, claim_timeout=60
    )
    scoped_ids = {r.id for r in rows if r.workspace_id == scope["workspace_id"]}

    assert pausing_null.id in scoped_ids
    assert running_stale.id not in scoped_ids
    assert paused_stale.id not in scoped_ids


async def test_touch_provider_check_removes_row_from_next_window(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """``touch_provider_check`` stamps now() -> row no longer matches the
    selection within ``claim_timeout`` seconds.
    """
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="pausing", last_provider_check=None)

    pre = await UserSandboxRepository.list_transient_for_reconcile_system(
        db_session, claim_timeout=60
    )
    assert row.id in {r.id for r in pre}

    await repo.touch_provider_check(row.id)

    post = await UserSandboxRepository.list_transient_for_reconcile_system(
        db_session, claim_timeout=60
    )
    assert row.id not in {r.id for r in post}

    await db_session.refresh(row)
    assert row.last_provider_check is not None
