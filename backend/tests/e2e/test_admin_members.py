"""E2E tests for org member management routes (/admin/members)."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy import select
from sqlmodel import col

from cubeplex.auth.users import UserManager
from cubeplex.models import (
    Conversation,
    ConversationExecutionAdmission,
    Membership,
    OrganizationMembership,
    OrgRole,
    Role,
    User,
    Workspace,
)
from cubeplex.repositories import MembershipRepository, OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


async def test_list_org_members(admin_client, session_factory):
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/members")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    first = data[0]
    assert "user_id" in first
    assert "email" in first
    assert "role" in first
    assert "created_at" in first


async def test_add_org_member_route_is_not_available(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": "person@example.com", "role": "member"},
    )
    assert resp.status_code == 405, resp.text


async def test_change_owner_role_returns_409(admin_client):
    client, _ws = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.patch(
        f"/api/v1/admin/members/{my_id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 409


async def test_remove_self_returns_400(admin_client):
    client, _ws = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.delete(f"/api/v1/admin/members/{my_id}")
    assert resp.status_code == 400


async def test_member_user_cannot_manage_org_members(member_client):
    client, _ws = member_client
    resp = await client.get("/api/v1/admin/members")
    assert resp.status_code == 403


async def test_remove_org_member_revokes_execution_in_each_workspace(admin_client, session_factory):
    client, ws_id = admin_client
    async with session_factory() as session:
        workspace = await session.get(Workspace, ws_id)
        assert workspace is not None
        user = await UserManager(SQLAlchemyUserDatabase(session, User)).create(
            BaseUserCreate(
                email=f"removed-org-{secrets.token_hex(4)}@example.com",
                password="test12345",
            ),
            safe=False,
        )
        await OrganizationMembershipRepository(session).grant(
            user_id=user.id,
            org_id=workspace.org_id,
            role=OrgRole.MEMBER,
        )
        await MembershipRepository(session).grant(
            user_id=user.id,
            workspace_id=ws_id,
            role=Role.MEMBER,
        )
        conversation = Conversation(
            org_id=workspace.org_id,
            workspace_id=ws_id,
            creator_user_id=user.id,
            title="org removal work",
        )
        session.add(conversation)
        await session.flush()
        admission = ConversationExecutionAdmission(
            org_id=workspace.org_id,
            workspace_id=ws_id,
            conversation_id=conversation.id,
            actor_user_id=user.id,
            execution_generation=0,
            source_kind="user_message",
            source_id=f"web:{secrets.token_hex(8)}",
            run_id=secrets.token_hex(16),
        )
        session.add(admission)
        await session.commit()
        user_id, admission_id, org_id = user.id, admission.id, workspace.org_id

    response = await client.delete(f"/api/v1/admin/members/{user_id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": True, "cleanup_pending": True}
    async with session_factory() as session:
        revoked = await session.get(ConversationExecutionAdmission, admission_id)
        assert revoked is not None and revoked.revoked_at is not None
        assert (
            await session.scalar(
                select(Membership).where(
                    col(Membership.user_id) == user_id,
                    col(Membership.workspace_id) == ws_id,
                )
            )
            is None
        )
        assert (
            await session.scalar(
                select(OrganizationMembership).where(
                    col(OrganizationMembership.user_id) == user_id,
                    col(OrganizationMembership.org_id) == org_id,
                )
            )
            is None
        )
