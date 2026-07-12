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


async def test_admin_me_picks_own_org_when_added_to_older_foreign_workspace(
    admin_client, session_factory
):
    """A user who joined an older foreign workspace must still resolve to their own org.

    Regression: `resolve_current_org_id` previously picked the user's first
    joined workspace's org, which broke once cross-org workspace membership
    landed — a user added to another org's older workspace was reported as
    `is_admin=false` of THAT org instead of owner of their own.
    """
    import secrets

    from fastapi_users.db import SQLAlchemyUserDatabase
    from fastapi_users.schemas import BaseUserCreate
    from sqlalchemy import select

    from cubeplex.auth.users import UserManager
    from cubeplex.models import Role, User, Workspace
    from cubeplex.models.organization_membership import OrgRole
    from cubeplex.repositories import MembershipRepository, OrganizationMembershipRepository

    client, ws_a = admin_client
    me_a = (await client.get("/api/v1/auth/me")).json()
    a_user_id: str = me_a["id"]

    async with session_factory() as session:
        ws_a_row = (
            await session.execute(select(Workspace).where(Workspace.id == ws_a))
        ).scalar_one()
        org_a = ws_a_row.org_id

        email_b = f"new-owner-{secrets.token_hex(4)}@example.com"
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        b = await manager.create(BaseUserCreate(email=email_b, password="test12345"), safe=False)
        b_id = b.id

        # B is added to A's org (and A's workspace) by A. B was registered LATER
        # so the bootstrap might or might not have created an own org; we don't
        # care for this test — what matters is the foreign membership comes
        # first by created_at-of-workspace.
        await OrganizationMembershipRepository(session).grant(
            user_id=b_id, org_id=org_a, role=OrgRole.MEMBER
        )
        await MembershipRepository(session).grant(user_id=b_id, workspace_id=ws_a, role=Role.MEMBER)

    # Login as B
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email_b, "password": "test12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code in (200, 204), resp.text

    me_resp = await client.get("/api/v1/admin/me")
    assert me_resp.status_code == 200, me_resp.text
    body = me_resp.json()
    # B is owner of their own org, not A's — even though A's workspace is older.
    assert body["org_id"] != org_a, body
    assert body["is_admin"] is True, body
    # cleanup: avoid touching A's user assertions in this client
    assert a_user_id != b_id


async def test_admin_me_uses_org_membership_not_workspace_admin(member_client, session_factory):
    """A workspace-admin who is NOT an org admin reports is_admin=false."""
    client, workspace_id = member_client
    from sqlalchemy import select

    from cubeplex.models import Membership, Role, User, Workspace
    from cubeplex.models.organization_membership import OrgRole
    from cubeplex.repositories import (
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
