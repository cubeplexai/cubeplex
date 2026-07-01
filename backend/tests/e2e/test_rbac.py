"""E2E RBAC tests: admin mutation, member denial, path-based workspace scoping."""

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_admin_can_rename_workspace(admin_client):
    client, workspace_id = admin_client
    r = await client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"name": "renamed-by-admin"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_member_cannot_rename_workspace(member_client):
    client, workspace_id = member_client
    r = await client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"name": "renamed-by-member"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_unaffiliated_workspace_returns_404(admin_client):
    client, _ = admin_client
    r = await client.get("/api/v1/ws/ws-does-not-exist/conversations")
    # Workspace-not-found yields 404 before the role/membership check (which
    # would yield 403). Intentional — avoids workspace id enumeration.
    assert r.status_code == 404, r.text
