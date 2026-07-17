"""E2E for SandboxManager in-use lease wrappers.

Verifies:
- ``renew_lease`` populates ``in_use_until`` in the future so
  ``list_idle_to_pause_system`` excludes the row; ``release_lease`` clears it.
- ``renew_lease`` accepts a custom ``lease_seconds`` longer than the manager's
  default and produces an expiry past the default window.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.manager import SandboxManager

_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])

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
        email = f"sbx-lease-{secrets.token_hex(4)}@example.com"
        password = secrets.token_urlsafe(16)
        org_name = f"Org {email}"
        org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
        ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
        await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
        await session.commit()
        return {"org_id": org.id, "workspace_id": ws.id, "user_id": user.id}


async def _seed_idle_running(
    db_session: AsyncSession,
    scope: dict[str, str],
) -> UserSandbox:
    """Seed a stale-idle running row that would otherwise be picked up by
    ``list_idle_to_pause_system``."""
    repo = UserSandboxRepository(
        db_session, org_id=scope["org_id"], workspace_id=scope["workspace_id"]
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
    return await repo.add(row)


async def test_renew_lease_excludes_row_from_pause_candidates_then_release(
    db_session: AsyncSession,
    db_session_maker: async_sessionmaker[AsyncSession],
    scope: dict[str, str],
) -> None:
    """(a) renew_lease populates in_use_until in the future; the system query
    excludes the row; release_lease clears it and the row reappears."""
    row = await _seed_idle_running(db_session, scope)
    sandbox_id = row.sandbox_id

    manager = SandboxManager(db_session_maker, _ENCRYPTION_BACKEND)

    await manager.renew_lease(
        sandbox_id,
        org_id=scope["org_id"],
        workspace_id=scope["workspace_id"],
    )

    await db_session.refresh(row)
    assert row.in_use_until is not None
    assert row.in_use_until > datetime.now(UTC)

    # System query should now skip this row.
    candidates = await UserSandboxRepository.list_idle_to_pause_system(
        db_session, idle_ttl_seconds=1
    )
    assert all(c.id != row.id for c in candidates)

    await manager.release_lease(
        sandbox_id,
        org_id=scope["org_id"],
        workspace_id=scope["workspace_id"],
    )

    await db_session.refresh(row)
    assert row.in_use_until is None

    candidates = await UserSandboxRepository.list_idle_to_pause_system(
        db_session, idle_ttl_seconds=1
    )
    assert any(c.id == row.id for c in candidates)


async def test_renew_lease_custom_seconds_exceeds_default_window(
    db_session: AsyncSession,
    db_session_maker: async_sessionmaker[AsyncSession],
    scope: dict[str, str],
) -> None:
    """(b) A custom lease_seconds longer than the default produces a future
    expiry past the default window."""
    row = await _seed_idle_running(db_session, scope)
    sandbox_id = row.sandbox_id

    manager = SandboxManager(db_session_maker, _ENCRYPTION_BACKEND)
    default_window = manager._lease_seconds
    custom = default_window * 4

    before = datetime.now(UTC)
    await manager.renew_lease(
        sandbox_id,
        org_id=scope["org_id"],
        workspace_id=scope["workspace_id"],
        lease_seconds=custom,
    )

    await db_session.refresh(row)
    assert row.in_use_until is not None
    assert row.in_use_until > before + timedelta(seconds=default_window)
