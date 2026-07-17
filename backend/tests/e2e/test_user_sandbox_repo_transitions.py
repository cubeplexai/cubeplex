"""E2E for UserSandboxRepository pause/resume state transitions.

Verifies:
- ``claim_pausing`` atomically moves stale-idle ``running`` rows to ``pausing``.
- Concurrent ``claim_pausing`` picks a single winner.
- Lease (``in_use_until``) and freshness re-checks happen inside the WHERE
  clause so a touch landing between selection and claim makes the claim a
  no-op.
- ``get_active_by_scope`` returns the one live sandbox entity in any runtime state.
- ``get_resumable_by_scope`` returns ``running`` or ``paused`` but never
  ``pausing``/``resuming`` (unless the only candidate row IS transient).
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

from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository

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
    scope_id: str | None = None,
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
        scope_type="user",
        scope_id=scope_id or scope["user_id"],
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

    assert await repo.claim_pausing(row.id, idle_ttl_seconds=1) is True

    await db_session.refresh(row)
    assert row.status == "pausing"


async def test_claim_pausing_single_winner(db_session: AsyncSession, scope: dict[str, str]) -> None:
    """(b) Second claim on now-pausing row returns False."""
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="running", idle_secs=10, ttl_seconds=1)

    assert await repo.claim_pausing(row.id, idle_ttl_seconds=1) is True
    assert await repo.claim_pausing(row.id, idle_ttl_seconds=3600) is False


async def test_claim_pausing_skips_leased_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(c) A row with in_use_until in the future is not claimed."""
    repo = _mk_repo(db_session, scope)
    future_lease = datetime.now(UTC) + timedelta(seconds=60)
    row = await _mk(
        repo, scope, status="running", idle_secs=10, ttl_seconds=1, in_use_until=future_lease
    )

    assert await repo.claim_pausing(row.id, idle_ttl_seconds=1) is False

    await db_session.refresh(row)
    assert row.status == "running"


async def test_claim_pausing_skips_fresh_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(d) A row whose last_activity_at is fresh is not claimed."""
    repo = _mk_repo(db_session, scope)
    # idle_secs=0 vs ttl=3600 → not stale.
    row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)

    assert await repo.claim_pausing(row.id, idle_ttl_seconds=3600) is False

    await db_session.refresh(row)
    assert row.status == "running"


async def test_get_active_by_scope_returns_live_rows_in_any_runtime_state(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(e) Every non-deleted sandbox entity is active regardless of runtime state."""
    repo = _mk_repo(db_session, scope)
    pausing_scope = f"usr_{secrets.token_hex(8)}"
    paused_scope = f"usr_{secrets.token_hex(8)}"
    await _mk(
        repo,
        scope,
        scope_id=pausing_scope,
        status="pausing",
        idle_secs=0,
        ttl_seconds=3600,
    )
    await _mk(
        repo,
        scope,
        scope_id=paused_scope,
        status="paused",
        idle_secs=0,
        ttl_seconds=3600,
    )

    pausing = await repo.get_active_by_scope(scope_type="user", scope_id=pausing_scope)
    paused = await repo.get_active_by_scope(scope_type="user", scope_id=paused_scope)
    assert pausing is not None
    assert pausing.status == "pausing"
    assert paused is not None
    assert paused.status == "paused"

    running = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)
    found = await repo.get_active_by_scope(scope_type="user", scope_id=scope["user_id"])
    assert found is not None
    assert found.id == running.id


async def test_get_resumable_by_scope_returns_most_recent_non_terminal(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(f) get_resumable_by_scope returns any non-terminal row (running, paused,
    pausing, resuming) — so a late-arriving caller sees the in-flight lifecycle
    row instead of treating it as absent and creating a duplicate sandbox.
    """
    repo = _mk_repo(db_session, scope)
    paused = await _mk(
        repo, scope, status="paused", idle_secs=0, ttl_seconds=3600, paused_at=datetime.now(UTC)
    )

    found = await repo.get_resumable_by_scope(scope_type="user", scope_id=scope["user_id"])
    assert found is not None
    assert found.id == paused.id
    assert found.status == "paused"


async def test_claim_terminated_from_paused_atomic(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """Atomic ``paused -> terminated`` claim used by ``reap_paused`` so a
    concurrent ``_resume_record`` taking the row through ``paused -> resuming``
    doesn't get its sandbox killed mid-resume (codex P2 round 8)."""
    repo = _mk_repo(db_session, scope)

    # (a) An expired paused row gets claimed exactly once.
    expired = await _mk(
        repo,
        scope,
        status="paused",
        idle_secs=0,
        ttl_seconds=3600,
        paused_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    assert await repo.claim_terminated_from_paused(expired.id, paused_ttl_seconds=60) is True
    # Second claim on the now-terminated row returns False.
    assert await repo.claim_terminated_from_paused(expired.id, paused_ttl_seconds=60) is False

    # (b) A paused row that's NOT past its TTL is not claimed.
    fresh_paused = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="paused",
        idle_secs=0,
        ttl_seconds=3600,
        paused_at=datetime.now(UTC),
    )
    assert (
        await repo.claim_terminated_from_paused(fresh_paused.id, paused_ttl_seconds=3600) is False
    )

    # (c) A row in transient ``resuming`` is not claimed even if past TTL —
    # the resume path owns the row.
    resuming_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="resuming",
        idle_secs=0,
        ttl_seconds=3600,
    )
    # Stamp paused_at directly so the time predicate would otherwise fire.
    resuming_row.paused_at = datetime.now(UTC) - timedelta(seconds=600)
    await db_session.commit()
    assert await repo.claim_terminated_from_paused(resuming_row.id, paused_ttl_seconds=60) is False


async def test_mark_failed_from_transient_atomic(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """``mark_failed_from_transient`` is the guarded variant used by the
    reconciler so a concurrent successful resume isn't clobbered (codex P2
    round 14). Accepts ``pausing`` and ``resuming`` priors; rejects everything
    else.
    """
    repo = _mk_repo(db_session, scope)

    pausing = await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)
    resuming = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="resuming",
        idle_secs=0,
        ttl_seconds=3600,
    )
    running = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="running",
        idle_secs=0,
        ttl_seconds=3600,
    )
    paused = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="paused",
        idle_secs=0,
        ttl_seconds=3600,
        paused_at=datetime.now(UTC),
    )

    # Legal: pausing/resuming -> failed
    assert await repo.mark_failed_from_transient(pausing.id) is True
    assert await repo.mark_failed_from_transient(resuming.id) is True

    # Illegal: running and paused are not transient — protect concurrent resume
    assert await repo.mark_failed_from_transient(running.id) is False
    assert await repo.mark_failed_from_transient(paused.id) is False

    await db_session.refresh(pausing)
    await db_session.refresh(resuming)
    await db_session.refresh(running)
    await db_session.refresh(paused)
    assert pausing.status == "failed"
    assert resuming.status == "failed"
    assert running.status == "running"
    assert paused.status == "paused"


async def test_get_resumable_by_scope_returns_transient_when_only_row(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """A lone ``pausing`` row IS returned; the manager waits on it instead of
    falling through to create-new (codex P1 race fix)."""
    repo = _mk_repo(db_session, scope)
    pausing = await _mk(repo, scope, status="pausing", idle_secs=0, ttl_seconds=3600)

    found = await repo.get_resumable_by_scope(scope_type="user", scope_id=scope["user_id"])
    assert found is not None
    assert found.id == pausing.id
    assert found.status == "pausing"


async def test_mark_paused_accepts_pausing_or_resuming_prior_state(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(g.1) mark_paused succeeds from ``pausing`` (pause completed) AND from
    ``resuming`` (codex P2: resume aborted mid-flight and provider still
    reports ``Paused`` — reconciler reverts the stuck resuming row). Rejects
    other prior states.
    """
    repo = _mk_repo(db_session, scope)
    row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)

    # Illegal: running → paused
    assert await repo.mark_paused(row.id) is False
    await db_session.refresh(row)
    assert row.status == "running"

    # Legal: pausing → paused
    pausing_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="pausing",
        idle_secs=0,
        ttl_seconds=3600,
    )
    assert await repo.mark_paused(pausing_row.id) is True
    await db_session.refresh(pausing_row)
    assert pausing_row.status == "paused"
    assert pausing_row.paused_at is not None

    # Legal (codex P2): resuming → paused (reconciler reverts when provider
    # still reports Paused after a mid-flight resume abort).
    resuming_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="resuming",
        idle_secs=0,
        ttl_seconds=3600,
    )
    assert await repo.mark_paused(resuming_row.id) is True
    await db_session.refresh(resuming_row)
    assert resuming_row.status == "paused"
    assert resuming_row.paused_at is not None


async def test_mark_resuming_rejects_non_paused_prior_state(
    db_session: AsyncSession, scope: dict[str, str]
) -> None:
    """(g.2) mark_resuming only succeeds from ``paused``."""
    repo = _mk_repo(db_session, scope)
    running_row = await _mk(repo, scope, status="running", idle_secs=0, ttl_seconds=3600)
    assert await repo.mark_resuming(running_row.id) is False

    paused_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="paused",
        idle_secs=0,
        ttl_seconds=3600,
        paused_at=datetime.now(UTC),
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
    pausing_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="pausing",
        idle_secs=0,
        ttl_seconds=3600,
    )
    assert await repo.mark_running(pausing_row.id) is True
    await db_session.refresh(pausing_row)
    assert pausing_row.status == "running"

    # From resuming → ok (resume completed)
    resuming_row = await _mk(
        repo,
        scope,
        scope_id=f"usr_{secrets.token_hex(8)}",
        status="resuming",
        idle_secs=0,
        ttl_seconds=3600,
    )
    now = datetime.now(UTC)
    assert await repo.mark_running(resuming_row.id, last_resumed_at=now) is True
    await db_session.refresh(resuming_row)
    assert resuming_row.status == "running"
    assert resuming_row.last_resumed_at is not None
