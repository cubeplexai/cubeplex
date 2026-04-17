"""E2E RBAC tests: admin mutation, member denial, workspace header requirements."""

import pytest


@pytest.mark.asyncio
async def test_admin_can_create_invite(admin_client):
    client, headers = admin_client
    workspace_id = headers["X-Workspace-Id"]
    r = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "token" in body


@pytest.mark.asyncio
async def test_member_cannot_create_invite(member_client):
    client, headers = member_client
    workspace_id = headers["X-Workspace-Id"]
    r = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_no_workspace_header_returns_400(admin_client):
    client, _ = admin_client
    if "X-Workspace-Id" in client.headers:
        del client.headers["X-Workspace-Id"]
    r = await client.get("/api/v1/conversations")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_unaffiliated_workspace_returns_404_or_403(admin_client):
    client, _ = admin_client
    # Clear default then send a bogus workspace id
    if "X-Workspace-Id" in client.headers:
        del client.headers["X-Workspace-Id"]
    r = await client.get("/api/v1/conversations", headers={"X-Workspace-Id": "ws-does-not-exist"})
    # Per request_context dependency, workspace-not-found yields 404 before
    # role/membership check (which would yield 403).
    assert r.status_code == 404, r.text
