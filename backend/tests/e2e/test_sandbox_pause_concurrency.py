"""Concurrency tests for pause/resume race guards.

Two scenarios:

1. ``claim_pausing`` race — two concurrent claims on the same stale-idle
   ``running`` row in independent sessions: exactly one returns True.
2. Double-resume guard — two overlapping ``_resume_record`` calls on one
   ``paused`` row: ``mark_resuming`` only succeeds once, so the provider
   resume runs exactly once. The loser polls and connects to the same
   sandbox via ``opensandbox.Sandbox.connect``. Winner-fails case: loser
   returns None so the caller can create a fresh one.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from opensandbox.config import ConnectionConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.manager import SandboxManager

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
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
        email = f"sbx-conc-{secrets.token_hex(4)}@example.com"
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
# Test 1 — claim_pausing race: parallel claims pick a single winner.
# ---------------------------------------------------------------------------


async def test_claim_pausing_concurrent_single_winner(
    db_session_maker: async_sessionmaker[AsyncSession], scope: dict[str, str]
) -> None:
    """Two concurrent ``claim_pausing`` calls in independent sessions on the
    same stale-idle ``running`` row: exactly one returns True.

    Independent sessions are required so the DB sees true overlap; a single
    session would serialise the updates inside one transaction.
    """
    # Seed a stale-idle, unleased running row.
    async with db_session_maker() as setup:
        setup_repo = UserSandboxRepository(
            setup, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
        )
        row = UserSandbox(
            user_id=scope["user_id"],
            scope_type="user",
            scope_id=scope["user_id"],
            sandbox_id=f"sbx_{secrets.token_hex(6)}",
            image="img:latest",
            status="running",
            ttl_seconds=1,
            last_activity_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        row = await setup_repo.add(row)
        await setup.commit()
        row_id = row.id

    async def claim() -> bool:
        async with db_session_maker() as s:
            repo = UserSandboxRepository(
                s, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
            )
            return await repo.claim_pausing(row_id, idle_ttl_seconds=1)

    r1, r2 = await asyncio.gather(claim(), claim())
    assert sorted([r1, r2]) == [False, True]

    # Verify final row state is pausing — never handed out for use mid-claim.
    async with db_session_maker() as verify:
        verify_repo = UserSandboxRepository(
            verify, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
        )
        final = await verify_repo.get(row_id)
        assert final is not None
        assert final.status == "pausing"


# ---------------------------------------------------------------------------
# Test 2 — Double-resume guard: provider resume runs exactly once.
# ---------------------------------------------------------------------------


def _make_manager() -> tuple[SandboxManager, MagicMock]:
    """Build a SandboxManager whose session_factory yields a usable mock
    session, so ``_await_resumed_by_winner``'s per-poll ``async with
    self._session_factory()`` works."""
    poll_session = MagicMock(name="poll_session")

    @asynccontextmanager
    async def _cm() -> AsyncIterator[Any]:
        yield poll_session

    factory = MagicMock(name="session_factory")
    factory.side_effect = lambda: _cm()
    mgr = SandboxManager(factory, FernetBackend([Fernet.generate_key()]))
    mgr._resume_timeout = 5
    # Force the egress branch off — keeps the test focused on the race.
    mgr._exchange_host = ""
    return mgr, poll_session


def _paused_record(scope: dict[str, str]) -> UserSandbox:
    return UserSandbox(
        user_id=scope["user_id"],
        scope_type="user",
        scope_id=scope["user_id"],
        sandbox_id="sbx_resume_target",
        image="img:latest",
        status="paused",
        ttl_seconds=600,
        last_activity_at=datetime.now(UTC),
        paused_at=datetime.now(UTC),
    )


async def test_double_resume_guard_winner_succeeds_loser_connects(
    scope: dict[str, str],
) -> None:
    """Two overlapping ``_resume_record`` calls on a single paused row:

    - ``mark_resuming`` returns True once, False the second time.
    - ``OpenSandbox.connect_or_resume`` is invoked exactly once (winner).
    - The loser polls, observes ``running``, and uses
      ``opensandbox.Sandbox.connect`` to attach to the *same* sandbox_id —
      both calls return a handle wrapping that one sandbox.
    """
    mgr, _poll_session = _make_manager()
    session = MagicMock(spec=AsyncSession)
    record = _paused_record(scope)
    conn_config = ConnectionConfig(domain="example.invalid")

    # Repo: mark_resuming wins once then loses; get() reports running once the
    # winner flips status. update_activity / mark_running are no-ops we just
    # count.
    repo = MagicMock(spec=UserSandboxRepository)
    repo.mark_resuming = AsyncMock(side_effect=[True, False])
    repo.mark_running = AsyncMock(return_value=True)
    repo.update_activity = AsyncMock()
    # Loser's poll: first poll observes running (winner already finished).
    running_view = _paused_record(scope)
    running_view.status = "running"
    repo.get = AsyncMock(return_value=running_view)

    winner_backend = MagicMock(name="winner_backend")
    loser_raw = MagicMock(name="loser_raw_sandbox")

    with (
        patch(
            "cubeplex.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=winner_backend),
        ) as cor,
        patch(
            "cubeplex.sandbox.manager.opensandbox.Sandbox.connect",
            new=AsyncMock(return_value=loser_raw),
        ) as raw_connect,
        # The loser's _await_resumed_by_winner constructs a fresh repo per
        # poll iteration so the identity-map cache can't hide the winner's
        # committed status. Redirect that construction to the test's repo.
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo),
    ):
        # Winner first; loser second. Both share the same record/session/repo.
        winner = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config,
            org_id=scope["org_id"],
            workspace_id=scope["workspace_id"],
            user_id=scope["user_id"],
        )
        loser = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config,
            org_id=scope["org_id"],
            workspace_id=scope["workspace_id"],
            user_id=scope["user_id"],
        )

    assert repo.mark_resuming.await_count == 2
    # Provider resume ran exactly once (no duplicate sandbox creation).
    assert cor.await_count == 1
    # Loser attached to the same sandbox via plain connect — not a new resume.
    assert raw_connect.await_count == 1
    args, kwargs = raw_connect.await_args
    assert args[0] == record.sandbox_id
    # Winner got the resumed backend; loser got a fresh wrapper around the
    # same underlying raw sandbox.
    assert winner is winner_backend
    assert loser is not None
    assert loser._sandbox is loser_raw


async def test_double_resume_guard_winner_fails_reconciler_settles_loser_bails(
    scope: dict[str, str],
) -> None:
    """Winner's provider resume raises → ``_resume_record`` returns None
    WITHOUT terminalizing the row (codex P1 round 13: client exceptions are
    ambiguous, so leave the row at ``resuming`` for the reconciler). The
    reconciler later observes provider ``Failed`` and marks the row
    ``failed``. The loser's wait helper observes ``failed`` and returns
    None; no second ``connect_or_resume`` happens.
    """
    mgr, _poll_session = _make_manager()
    session = MagicMock(spec=AsyncSession)
    record = _paused_record(scope)
    conn_config = ConnectionConfig(domain="example.invalid")

    # Sequence ``repo.get`` returns: the row sits at ``resuming`` while the
    # reconciler is still working, then becomes ``failed`` once the reconciler
    # commits. (In the unit test we don't run the reconciler; we just sequence
    # the mock to reflect what the loser would observe over time.)
    resuming_view = _paused_record(scope)
    resuming_view.status = "resuming"
    failed_view = _paused_record(scope)
    failed_view.status = "failed"

    repo = MagicMock(spec=UserSandboxRepository)
    repo.mark_resuming = AsyncMock(side_effect=[True, False])
    repo.mark_failed_from_resuming = AsyncMock(return_value=True)
    repo.mark_failed = AsyncMock()
    repo.mark_running = AsyncMock(return_value=True)
    repo.update_activity = AsyncMock()
    repo.get = AsyncMock(side_effect=[resuming_view, failed_view, failed_view])

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError("provider resume blew up")

    with (
        patch(
            "cubeplex.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(side_effect=_raise),
        ) as cor,
        patch(
            "cubeplex.sandbox.manager.opensandbox.Sandbox.connect",
            new=AsyncMock(),
        ) as raw_connect,
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo),
    ):
        winner = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config,
            org_id=scope["org_id"],
            workspace_id=scope["workspace_id"],
            user_id=scope["user_id"],
        )
        loser = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config,
            org_id=scope["org_id"],
            workspace_id=scope["workspace_id"],
            user_id=scope["user_id"],
        )

    assert winner is None
    assert loser is None
    # Provider was attempted exactly once (winner only).
    assert cor.await_count == 1
    # Loser observed terminal state via repo.get; no plain connect either.
    assert raw_connect.await_count == 0
    # Winner did NOT terminalize the row — reconciler owns that transition.
    repo.mark_failed_from_resuming.assert_not_called()
    repo.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — Transient-wait timeout raises instead of allowing duplicate create.
# ---------------------------------------------------------------------------


async def test_await_stable_status_raises_on_timeout(scope: dict[str, str]) -> None:
    """If a transient row never settles within ``_resume_timeout``, the wait
    helper must raise ``SandboxError`` rather than return None — otherwise
    ``get_or_create`` would silently provision a duplicate sandbox while the
    original lifecycle operation is still in flight (codex P2 round 3).
    """
    from cubeplex.sandbox.base import SandboxError

    mgr, _poll_session = _make_manager()
    mgr._resume_timeout = 1  # bound the wait so the test runs fast

    # Repo whose `get` always sees a still-pausing row.
    stuck = _paused_record(scope)
    stuck.status = "pausing"
    repo = MagicMock(spec=UserSandboxRepository)
    repo.get = AsyncMock(return_value=stuck)

    with patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo):
        with pytest.raises(SandboxError, match="did not settle"):
            await mgr._await_stable_status(
                "rec-1",
                org_id=scope["org_id"],
                workspace_id=scope["workspace_id"],
            )


# ---------------------------------------------------------------------------
# Test 4 — Resume loser observing ``paused`` (winner's row reverted) takes
# over the resume instead of falling through to create-new (codex P1 round 7).
# ---------------------------------------------------------------------------


async def test_await_resumed_by_winner_takes_over_on_paused_revert(
    scope: dict[str, str],
) -> None:
    """When the winner's resume is reverted ``resuming -> paused`` by the
    reconciler (mid-flight), the loser sees ``paused`` on its next poll. The
    loser must NOT bail and create a duplicate — it must take over by calling
    ``_resume_record`` itself, which atomically re-claims ``paused -> resuming``
    and completes the resume.
    """
    mgr, _poll_session = _make_manager()

    paused_view = _paused_record(scope)
    paused_view.status = "paused"

    # Provide a repo for both the wait helper's fresh-session poll AND the
    # take-over ``_resume_record`` call.
    repo = MagicMock(spec=UserSandboxRepository)
    repo.get = AsyncMock(return_value=paused_view)
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=True)
    repo.update_activity = AsyncMock()
    repo.mark_failed = AsyncMock()

    backend = MagicMock(name="takeover_backend")

    with (
        patch(
            "cubeplex.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ) as cor,
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=repo),
    ):
        result = await mgr._await_resumed_by_winner(
            paused_view.id,
            ConnectionConfig(domain="example.invalid"),
            org_id=scope["org_id"],
            workspace_id=scope["workspace_id"],
            user_id=scope["user_id"],
        )

    # Loser took over and got back a live backend — no fallthrough to create.
    assert result is backend
    cor.assert_awaited_once()
    repo.mark_resuming.assert_awaited()
