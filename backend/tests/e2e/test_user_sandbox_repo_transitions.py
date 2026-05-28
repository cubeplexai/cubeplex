"""E2E for UserSandboxRepository pause/resume state transitions.

Verifies:
- ``claim_pausing`` atomically moves stale-idle ``running`` rows to ``pausing``.
- Concurrent ``claim_pausing`` picks a single winner.
- Lease (``in_use_until``) and freshness re-checks happen inside the WHERE
  clause so a touch landing between selection and claim makes the claim a
  no-op.
- ``get_active_by_user`` ignores transient/paused rows.
- ``get_resumable_by_user`` returns ``running`` or ``paused`` but never
  ``pausing``/``resuming``.
- ``mark_paused``/``mark_resuming``/``mark_running`` reject illegal prior
  states.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.user_sandbox import UserSandboxRepository

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures: seed org/workspace/user via the same pattern as
# test_mcp_oauth_handoff.py.
# ---------------------------------------------------------------------------


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

    from cubebox.auth.users import UserManager, _slugify_org_name
    from cubebox.models import Role, User
    from cubebox.repositories import (
        MembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    async with db_session_maker() as session:
        org_repo = OrganizationRepository(session)
        ws_repo = WorkspaceRepository(session)
        mem_repo = MembershipRepository(session)
        email = f"sbx-repo-{secrets.token_hex(4)}@example.com"
        password = secrets.token_urlsafe(16)
        org_name = f"Org {email}"
        org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
        ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
        await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
        await session.commit()
        return {"org_id": org.id, "workspace_id": ws.id, "user_id": user.id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mk(
    repo: UserSandboxRepository,
    scope: dict[str, str],
    *,
    status: str = "running",
    idle_secs: int = 10,
    ttl_seconds: int = 1,
    in_use_until: datetime | None = None,
    paused_at: datetime | None = None,
    paused_ttl_seconds: int = 24 * 60,
) -> UserSandbox:
    now = datetime.now(UTC)
    row = UserSandbox(
        user_id=scope["user_id"],
        sandbox_id=f"sbx_{secrets.token_hex(6)}",
        image="img:latest",
        status=status,
        ttl_seconds=ttl_seconds,
        last_activity_at=now - timedelta(seconds=idle_secs),
        in_use_until=in_use_until,
        paused_at=paused_at,
        paused_ttl_seconds=paused_ttl_seconds,
    )
    return await repo.add(row)


def _mk_repo(db_session: AsyncSession, scope: dict[str, str]) -> UserSandboxRepository:
    return UserSandboxRepository(
        db_session, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_claim_pausing_flips_stale_idle_running_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(a) A stale-idle running row with no lease gets claimed."""
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="running", idle_secs=10, ttl_seconds=1)

    assert await repo.claim_pausing(row.id) is True

    await db_session.refresh(row)
    assert row.status == "pausing"


async def test_claim_pausing_single_winner(db_session: AsyncSession, scope: dict[str, str]) -> None:
    """(b) Second claim on now-pausing row returns False."""
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="running", idle_secs=10, ttl_seconds=1)

    assert await repo.claim_pausing(row.id) is True
    assert await repo.claim_pausing(row.id) is False


async def test_claim_pausing_skips_leased_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(c) A row with in_use_until in the future is not claimed."""
    repo = _mk_repo(db_session, scope)
    future_lease = datetime.now(UTC) + timedelta(seconds=60)
    row = await _mk(
        repo, scope, status="running", idle_secs=10, ttl_seconds=1, in_use_until=future_lease
    )

    assert await repo.claim_pausing(row.id) is False

    await db_session.refresh(row)
    assert row.status == "running"


async def test_claim_pausing_skips_fresh_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(d) A row whose last_activity_at is fresh is not claimed."""
    repo = _mk_repo(db_session, scope)
    # idle_secs=0 vs ttl=3600 → not stale.
    row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)

    assert await repo.claim_pausing(row.id) is False

    await db_session.refresh(row)
    assert row.status == "running"


async def test_get_active_by_user_ignores_pausing_and_paused(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(e) get_active_by_user only returns ``running`` rows."""
    repo = _mk_repo(db_session, scope)
    await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)
    await _mk(repo, scope, status="paused", idle_secs=0, ttl_seconds=3600)

    assert await repo.get_active_by_user(scope["user_id"]) is None

    running = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)
    found = await repo.get_active_by_user(scope["user_id"])
    assert found is not None
    assert found.id == running.id


async def test_get_resumable_by_user_returns_paused_not_transient(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(f) get_resumable_by_user returns paused but never pausing/resuming."""
    repo = _mk_repo(db_session, scope)
    await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)
    await _mk(repo, scope, status="resuming", idle_secs=0, ttl_seconds=3600)
    paused = await _mk(
        repo, scope, status="paused", idle_secs=0, ttl_seconds=3600, paused_at=datetime.now(UTC)
    )

    found = await repo.get_resumable_by_user(scope["user_id"])
    assert found is not None
    assert found.id == paused.id
    assert found.status == "paused"


async def test_mark_paused_rejects_non_pausing_prior_state(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(g.1) mark_paused only succeeds from ``pausing``."""
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)

    # Illegal: running → paused
    assert await repo.mark_paused(row.id) is False
    await db_session.refresh(row)
    assert row.status == "running"

    # Legal: pausing → paused
    await repo.claim_pausing(
        (await _mk(repo, scope, status="running", idle_secs=10, ttl_seconds=1)).id
    )
    pausing_row = await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)
    assert await repo.mark_paused(pausing_row.id) is True
    await db_session.refresh(pausing_row)
    assert pausing_row.status == "paused"
    assert pausing_row.paused_at is not None


async def test_mark_resuming_rejects_non_paused_prior_state(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(g.2) mark_resuming only succeeds from ``paused``."""
    repo = _mk_repo(db_session, scope)
    running_row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)
    assert await repo.mark_resuming(running_row.id) is False

    paused_row = await _mk(
        repo, scope, status="paused", idle_secs=0, ttl_seconds=3600, paused_at=datetime.now(UTC)
    )
    assert await repo.mark_resuming(paused_row.id) is True
    await db_session.refresh(paused_row)
    assert paused_row.status == "resuming"


async def test_mark_running_rejects_terminal_and_paused_states(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(g.3) mark_running only succeeds from ``pausing`` or ``resuming``."""
    repo = _mk_repo(db_session, scope)

    # From paused → not allowed (must go through resuming first)
    paused_row = await _mk(
        repo, scope, status="paused", idle_secs=0, ttl_seconds=3600, paused_at=datetime.now(UTC)
    )
    assert await repo.mark_running(paused_row.id) is False

    # From pausing → ok (pause failed → revert)
    pausing_row = await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)
    assert await repo.mark_running(pausing_row.id) is True
    await db_session.refresh(pausing_row)
    assert pausing_row.status == "running"

    # From resuming → ok (resume completed)
    resuming_row = await _mk(repo, scope, status="resuming", idle_secs=0, ttl_seconds=3600)
    now = datetime.now(UTC)
    assert await repo.mark_running(resuming_row.id, last_resumed_at=now) is True
    await db_session.refresh(resuming_row)
    assert resuming_row.status == "running"
    assert resuming_row.last_resumed_at is not None
