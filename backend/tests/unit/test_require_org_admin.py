"""Tests for require_org_admin FastAPI dependency.

`require_org_admin` doesn't take `workspace_id` path (admin routes aren't
workspace-scoped). It resolves the user's current org from their first
workspace membership, then checks the user's `OrganizationMembership.role`
in that org (M9 — replaced the legacy "admin in any workspace" rule).
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.auth.dependencies import require_org_admin
from cubebox.models import (  # noqa: F401
    Membership,
    Organization,
    OrganizationMembership,
    OrgRole,
    Role,
    User,
    Workspace,
)
from cubebox.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
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


async def test_passes_for_org_admin(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user = MagicMock(id=str(uuid4()))
    await MembershipRepository(session).grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=OrgRole.OWNER
    )

    result = await require_org_admin(user=user, session=session)
    assert result is user


async def test_raises_403_for_non_admin(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user = MagicMock(id=str(uuid4()))
    await MembershipRepository(session).grant(user_id=user.id, workspace_id=ws.id, role=Role.MEMBER)
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=OrgRole.MEMBER
    )

    with pytest.raises(HTTPException) as exc:
        await require_org_admin(user=user, session=session)
    assert exc.value.status_code == 403


async def test_raises_403_when_user_has_no_workspaces(session: AsyncSession) -> None:
    user = MagicMock(id=str(uuid4()))

    with pytest.raises(HTTPException) as exc:
        await require_org_admin(user=user, session=session)
    assert exc.value.status_code == 403


async def test_raises_403_when_workspace_admin_but_org_member(session: AsyncSession) -> None:
    """M9 regression: workspace-admin alone is not enough; OrganizationMembership.role drives the gate."""
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user = MagicMock(id=str(uuid4()))
    await MembershipRepository(session).grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=OrgRole.MEMBER
    )

    with pytest.raises(HTTPException) as exc:
        await require_org_admin(user=user, session=session)
    assert exc.value.status_code == 403
