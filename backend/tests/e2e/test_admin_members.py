"""E2E tests for org member management routes (/admin/members)."""

import pytest

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
