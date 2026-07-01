"""Tests for MembershipRepository.user_has_role_in_org (added by M2)."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# Ensure all tables are registered on SQLModel.metadata before create_all.
from cubebox.models import (  # noqa: F401
    Membership,
    Organization,
    Role,
    Workspace,
)
from cubebox.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_user_with_admin_membership_in_org_returns_true(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user_id = str(uuid4())
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is True


async def test_user_with_only_member_role_admin_check_returns_false(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user_id = str(uuid4())
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.MEMBER)

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is False


async def test_user_with_no_membership_in_org_returns_false(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    user_id = str(uuid4())
    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is False


async def test_admin_in_one_workspace_grants_org_admin(session: AsyncSession) -> None:
    """User who is ADMIN in any workspace of the org passes the check."""
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws_a = await WorkspaceRepository(session).create(org_id=org.id, name="A")
    ws_b = await WorkspaceRepository(session).create(org_id=org.id, name="B")
    user_id = str(uuid4())
    # Member in A, Admin in B → admin in org
    await MembershipRepository(session).grant(
        user_id=user_id, workspace_id=ws_a.id, role=Role.MEMBER
    )
    await MembershipRepository(session).grant(
        user_id=user_id, workspace_id=ws_b.id, role=Role.ADMIN
    )

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is True
