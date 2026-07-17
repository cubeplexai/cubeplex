"""E2E tests for workspace member management routes (/ws/{wsId}/members)."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.users import UserManager
from cubeplex.models import OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


async def _create_org_member(session: AsyncSession, org_id: str) -> User:
    """Create a user who is an org member but not in any workspace."""
    email = f"orgmember-{secrets.token_hex(4)}@example.com"
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    user = await manager.create(BaseUserCreate(email=email, password="test12345"), safe=False)
    om_repo = OrganizationMembershipRepository(session)
    await om_repo.grant(user_id=user.id, org_id=org_id, role=OrgRole.MEMBER)
    return user


async def _get_org_id(client, ws_id: str) -> str:
    resp = await client.get("/api/v1/workspaces")
    for ws in resp.json():
        if ws["id"] == ws_id:
            return ws["org_id"]
    raise ValueError(f"workspace {ws_id} not found")


async def test_list_workspace_members(admin_client):
    client, ws_id = admin_client
    resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "user_id" in data[0]
    assert "email" in data[0]
    assert "role" in data[0]


async def test_list_available_members(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    resp = await client.get(f"/api/v1/ws/{ws_id}/members/available")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    user_ids = [m["user_id"] for m in data]
    assert new_user.id in user_ids


async def test_add_workspace_member(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == new_user.id

    list_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id in [m["user_id"] for m in list_resp.json()]

    avail_resp = await client.get(f"/api/v1/ws/{ws_id}/members/available")
    assert new_user.id not in [m["user_id"] for m in avail_resp.json()]


async def test_add_non_org_member_returns_403(admin_client, session_factory):
    client, ws_id = admin_client
    async with session_factory() as session:
        email = f"outsider-{secrets.token_hex(4)}@example.com"
        user_db = SQLAlchemyUserDatabase(session, User)
        manager = UserManager(user_db)
        outsider = await manager.create(
            BaseUserCreate(email=email, password="test12345"), safe=False
        )

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": outsider.id, "role": "member"},
    )
    assert resp.status_code == 403


async def test_add_duplicate_returns_409(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(
        f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"}
    )
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )
    assert resp.status_code == 409


async def test_change_workspace_member_role(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(
        f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"}
    )
    resp = await client.patch(
        f"/api/v1/ws/{ws_id}/members/{new_user.id}/role",
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


async def test_remove_workspace_member(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(
        f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"}
    )
    resp = await client.delete(f"/api/v1/ws/{ws_id}/members/{new_user.id}")
    assert resp.status_code == 204

    list_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id not in [m["user_id"] for m in list_resp.json()]


async def test_remove_self_returns_400(admin_client):
    client, ws_id = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.delete(f"/api/v1/ws/{ws_id}/members/{my_id}")
    assert resp.status_code == 400


async def test_cannot_demote_last_admin_self(admin_client):
    client, ws_id = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.patch(
        f"/api/v1/ws/{ws_id}/members/{my_id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 400, resp.text


async def test_can_demote_self_when_another_admin_exists(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)
    async with session_factory() as session:
        other = await _create_org_member(session, org_id)

    await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": other.id, "role": "admin"},
    )
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.patch(
        f"/api/v1/ws/{ws_id}/members/{my_id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "member"


async def test_member_cannot_manage_workspace_members(member_client):
    client, ws_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": "usr-nonexistent", "role": "member"},
    )
    assert resp.status_code == 403
