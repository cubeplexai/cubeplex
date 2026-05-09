"""Fixtures for memory data-layer invariant tests."""

import secrets
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models import Role, User
from cubebox.models.organization import Organization
from cubebox.models.workspace import Workspace
from cubebox.repositories import MembershipRepository, OrganizationRepository, WorkspaceRepository


def _token(n: int = 6) -> str:
    return secrets.token_hex(n)


async def _make_org(session: AsyncSession) -> Organization:
    slug = f"org-{_token()}"
    return await OrganizationRepository(session).create(name=f"Org {slug}", slug=slug)


async def _make_user(session: AsyncSession) -> User:
    user = User(
        id=f"u-{_token(4)}",
        email=f"{_token(4)}@test.com",
        hashed_password="x",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_workspace(session: AsyncSession, org_id: str) -> Workspace:
    return await WorkspaceRepository(session).create(org_id=org_id, name=f"ws-{_token()}")


@pytest_asyncio.fixture
async def seed_user(db_session: AsyncSession) -> AsyncIterator[User]:
    """A single user seeded into the test DB."""
    user = await _make_user(db_session)
    yield user


@pytest_asyncio.fixture
async def seed_workspace(db_session: AsyncSession, seed_user: User) -> AsyncIterator[Workspace]:
    """An org + workspace owned by seed_user."""
    org = await _make_org(db_session)
    ws = await _make_workspace(db_session, org.id)
    # Patch workspace so tests can access org_id easily
    ws.org_id = org.id  # already set, just being explicit
    yield ws


@pytest_asyncio.fixture
async def seed_other_workspace_user(db_session: AsyncSession) -> AsyncIterator[User]:
    """A second user in a totally different org/workspace."""
    user = await _make_user(db_session)
    yield user


@pytest_asyncio.fixture
async def seed_two_workspaces(
    db_session: AsyncSession, seed_user: User
) -> AsyncIterator[tuple[Workspace, Workspace]]:
    """Two workspaces (in two different orgs) accessible to the same seed_user."""
    org_a = await _make_org(db_session)
    org_b = await _make_org(db_session)
    ws_a = await _make_workspace(db_session, org_a.id)
    ws_b = await _make_workspace(db_session, org_b.id)
    yield ws_a, ws_b


@pytest_asyncio.fixture
async def second_member_client(
    member_client: tuple[httpx.AsyncClient, str],
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """A second user in the SAME workspace as ``member_client``.

    Seeds a brand-new user (with their own personal org/workspace) and grants
    them membership in the primary member_client's workspace directly via the
    DB. The invite API requires admin role which the primary member does not
    hold, so we use the same DB-level grant pattern as _make_isolated_user.

    Yields ``(client, workspace_id)`` where workspace_id is the primary
    workspace so both clients target the same scope.
    """
    _primary_client, workspace_id = member_client

    # Import bootstrap helpers from the top-level e2e conftest.
    from tests.e2e.conftest import (  # type: ignore[import-not-found]
        _lifespan_context,
        _login_and_attach,
        _make_isolated_user,
    )

    # Create a brand-new isolated user (own org + personal workspace).
    app, email, password, _own_workspace_id = await _make_isolated_user(Role.MEMBER)
    app.state.deployment_mode = "multi_tenant"

    # Grant the second user membership in the PRIMARY workspace via DB.
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_maker() as session:
            # Look up the user by email to get their id.
            from sqlalchemy import select

            from cubebox.models import User as _User

            result = await session.execute(select(_User).where(_User.email == email))
            second_user = result.scalar_one()
            await MembershipRepository(session).grant(
                user_id=second_user.id, workspace_id=workspace_id, role=Role.MEMBER
            )
    finally:
        await engine.dispose()

    # Boot the app and log in as the second user.
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as second_client:
            await _login_and_attach(second_client, email, password)
            yield second_client, workspace_id
