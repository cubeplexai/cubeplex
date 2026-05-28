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
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from opensandbox.config import ConnectionConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.manager import SandboxManager

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
            return await repo.claim_pausing(row_id)

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


def _make_manager() -> SandboxManager:
    """Build a SandboxManager without touching real config — only the bits
    ``_resume_record`` / ``_await_resumed_by_winner`` read are needed."""
    session_factory = MagicMock(spec=async_sessionmaker)
    mgr = SandboxManager(session_factory)
    mgr._resume_timeout = 5
    # Force the egress branch off — keeps the test focused on the race.
    mgr._exchange_host = ""
    return mgr


def _paused_record(scope: dict[str, str]) -> UserSandbox:
    return UserSandbox(
        user_id=scope["user_id"],
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
    mgr = _make_manager()
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
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=winner_backend),
        ) as cor,
        patch(
            "cubebox.sandbox.manager.opensandbox.Sandbox.connect",
            new=AsyncMock(return_value=loser_raw),
        ) as raw_connect,
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


async def test_double_resume_guard_winner_fails_loser_returns_none(
    scope: dict[str, str],
) -> None:
    """Winner's provider resume raises → row is marked ``failed``. The loser's
    poll observes ``failed`` and returns ``None`` so the caller may create a
    fresh sandbox; no second ``connect_or_resume`` happens.
    """
    mgr = _make_manager()
    session = MagicMock(spec=AsyncSession)
    record = _paused_record(scope)
    conn_config = ConnectionConfig(domain="example.invalid")

    failed_view = _paused_record(scope)
    failed_view.status = "failed"

    repo = MagicMock(spec=UserSandboxRepository)
    repo.mark_resuming = AsyncMock(side_effect=[True, False])
    repo.mark_failed = AsyncMock()
    repo.mark_running = AsyncMock(return_value=True)
    repo.update_activity = AsyncMock()
    repo.get = AsyncMock(return_value=failed_view)

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError("provider resume blew up")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(side_effect=_raise),
        ) as cor,
        patch(
            "cubebox.sandbox.manager.opensandbox.Sandbox.connect",
            new=AsyncMock(),
        ) as raw_connect,
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
    # Provider was attempted exactly once (winner only); loser never retried.
    assert cor.await_count == 1
    # Loser saw failed via repo.get and bailed without connecting.
    assert raw_connect.await_count == 0
    repo.mark_failed.assert_awaited_once_with(record.id)
