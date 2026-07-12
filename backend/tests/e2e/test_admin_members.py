"""E2E tests for org member management routes (/admin/members)."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.users import UserManager
from cubeplex.models import User

pytestmark = pytest.mark.e2e


async def _create_standalone_user(session: AsyncSession) -> User:
    """Create a user not in any org (for add-member tests)."""
    email = f"standalone-{secrets.token_hex(4)}@example.com"
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    return await manager.create(BaseUserCreate(email=email, password="test12345"), safe=False)


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


async def test_add_org_member(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email"] == new_user.email
    assert data["role"] == "member"

    list_resp = await client.get("/api/v1/admin/members")
    emails = [m["email"] for m in list_resp.json()]
    assert new_user.email in emails


async def test_add_duplicate_member_returns_409(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "member"},
    )
    assert resp.status_code == 409


async def test_add_nonexistent_email_returns_404(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": "nobody-exists@example.com", "role": "member"},
    )
    assert resp.status_code == 404


async def test_add_invalid_role_returns_400(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "owner"},
    )
    assert resp.status_code == 400


async def test_change_member_role(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.patch(
        f"/api/v1/admin/members/{new_user.id}/role",
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


async def test_change_owner_role_returns_409(admin_client):
    client, _ws = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.patch(
        f"/api/v1/admin/members/{my_id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 409


async def test_remove_member(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.delete(f"/api/v1/admin/members/{new_user.id}")
    assert resp.status_code == 204

    list_resp = await client.get("/api/v1/admin/members")
    emails = [m["email"] for m in list_resp.json()]
    assert new_user.email not in emails


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


async def test_remove_cascades_workspace_memberships(admin_client, session_factory):
    client, ws_id = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )

    ws_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id in [m["user_id"] for m in ws_resp.json()]

    await client.delete(f"/api/v1/admin/members/{new_user.id}")

    ws_resp2 = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id not in [m["user_id"] for m in ws_resp2.json()]
