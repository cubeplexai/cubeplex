"""E2E test for GET /api/v1/admin/me."""

import pytest

pytestmark = pytest.mark.e2e


async def test_admin_user_gets_is_admin_true(admin_client):
    client, _workspace_id = admin_client
    resp = await client.get("/api/v1/admin/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_admin"] is True
    assert isinstance(data["org_id"], str) and data["org_id"]
    assert isinstance(data["org_name"], str) and data["org_name"]


async def test_member_user_gets_is_admin_false(member_client):
    client, _workspace_id = member_client
    resp = await client.get("/api/v1/admin/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_admin"] is False
    assert isinstance(data["org_id"], str) and data["org_id"]


async def test_unauthenticated_returns_401(unauthenticated_memory_client):
    resp = await unauthenticated_memory_client.get("/api/v1/admin/me")
    assert resp.status_code == 401, resp.text


async def test_admin_me_uses_org_membership_not_workspace_admin(member_client, session_factory):
    """A workspace-admin who is NOT an org admin reports is_admin=false."""
    client, workspace_id = member_client
    from sqlalchemy import select

    from cubebox.models import Membership, Role, User, Workspace
    from cubebox.models.organization_membership import OrgRole
    from cubebox.repositories import (
        MembershipRepository,
        OrganizationMembershipRepository,
        WorkspaceRepository,
    )

    me_resp = await client.get("/api/v1/auth/me")
    user_email = me_resp.json()["email"]

    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == user_email))).scalar_one()
        any_ws = (
            await session.execute(
                select(Workspace)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(Membership.user_id == user.id)
                .limit(1)
            )
        ).scalar_one()

        await OrganizationMembershipRepository(session).promote(
            user_id=user.id, org_id=any_ws.org_id, role=OrgRole.MEMBER
        )

        ws2 = await WorkspaceRepository(session).create(org_id=any_ws.org_id, name="user-owned-ws")
        await MembershipRepository(session).grant(
            user_id=user.id, workspace_id=ws2.id, role=Role.ADMIN
        )

    resp = await client.get("/api/v1/admin/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_admin"] is False
