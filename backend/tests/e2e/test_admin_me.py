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
