"""Fixtures for memory data-layer invariant tests."""

import secrets
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import User
from cubebox.models.organization import Organization
from cubebox.models.workspace import Workspace
from cubebox.repositories import OrganizationRepository, WorkspaceRepository


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
