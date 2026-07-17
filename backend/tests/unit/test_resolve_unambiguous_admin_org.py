"""Tests for resolve_unambiguous_admin_org_id.

Admin routes have no workspace path segment, so when a user has admin or
owner role in more than one org there is no structural cue for which org
the call targets. The unambiguous helper refuses the call with 400
instead of silently picking — which would route writes to the wrong org.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.auth.dependencies import resolve_unambiguous_admin_org_id
from cubeplex.llm.errors import AmbiguousOrgError
from cubeplex.models import (  # noqa: F401
    Membership,
    Organization,
    OrganizationMembership,
    OrgRole,
    Role,
    User,
    Workspace,
)
from cubeplex.repositories import (
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


async def test_single_org_admin_resolves(session: AsyncSession) -> None:
    """A user with a single admin-level membership resolves silently."""
    org = await OrganizationRepository(session).create(name="Acme", slug="acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user = MagicMock(id=str(uuid4()))
    await MembershipRepository(session).grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=OrgRole.OWNER
    )
    resolved = await resolve_unambiguous_admin_org_id(user, session)
    assert resolved == org.id


async def test_multi_org_member_with_one_admin_role_resolves(session: AsyncSession) -> None:
    """Admin in one org, plain member in another → no ambiguity, picks the admin org."""
    org_admin = await OrganizationRepository(session).create(name="Admin", slug="admin")
    org_member = await OrganizationRepository(session).create(name="Member", slug="member")
    ws_a = await WorkspaceRepository(session).create(org_id=org_admin.id, name="A")
    ws_m = await WorkspaceRepository(session).create(org_id=org_member.id, name="M")
    user = MagicMock(id=str(uuid4()))
    await MembershipRepository(session).grant(
        user_id=user.id, workspace_id=ws_a.id, role=Role.ADMIN
    )
    await MembershipRepository(session).grant(
        user_id=user.id, workspace_id=ws_m.id, role=Role.MEMBER
    )
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org_admin.id, role=OrgRole.ADMIN
    )
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org_member.id, role=OrgRole.MEMBER
    )
    resolved = await resolve_unambiguous_admin_org_id(user, session)
    assert resolved == org_admin.id


async def test_raises_when_admin_of_multiple_orgs(session: AsyncSession) -> None:
    """Admin in two orgs → 400 AmbiguousOrgError."""
    org_a = await OrganizationRepository(session).create(name="A", slug="a")
    org_b = await OrganizationRepository(session).create(name="B", slug="b")
    user = MagicMock(id=str(uuid4()))
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org_a.id, role=OrgRole.OWNER
    )
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org_b.id, role=OrgRole.ADMIN
    )

    with pytest.raises(AmbiguousOrgError) as exc:
        await resolve_unambiguous_admin_org_id(user, session)
    assert exc.value.status_code == 400
    assert exc.value.error_code == "ambiguous_org"
    assert set(exc.value.org_ids) == {org_a.id, org_b.id}
