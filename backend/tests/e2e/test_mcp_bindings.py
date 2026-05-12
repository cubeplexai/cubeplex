"""E2E tests for workspace overrides of org-wide MCP installs."""

import httpx


async def _create_second_workspace(client: httpx.AsyncClient, workspace_id: str) -> str:
    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    current = next(ws for ws in workspaces_resp.json() if ws["id"] == workspace_id)

    create_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "MCP Overrides Secondary", "org_id": current["org_id"]},
    )
    assert create_resp.status_code == 201, create_resp.text
    return create_resp.json()["id"]


def _overrides_by_workspace(rows: list[dict[str, object]]) -> dict[str, bool]:
    return {str(row["workspace_id"]): bool(row["enabled"]) for row in rows}


async def test_admin_overrides_control_workspace_visibility(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_a = admin_client
    workspace_b = await _create_second_workspace(client, workspace_a)

    create_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "overrides-org-wide",
            "server_url": "http://127.0.0.1:9/overrides-org-wide",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    # Default: neither workspace sees the org-wide install (invisible by default).
    list_a_resp = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    assert list_a_resp.status_code == 200
    assert all(server["id"] != server_id for server in list_a_resp.json()["inherited"])
    list_b_resp = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert list_b_resp.status_code == 200
    assert all(server["id"] != server_id for server in list_b_resp.json()["inherited"])

    # Enable for both workspaces via override.
    enable_a_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/overrides",
        json={"workspace_id": workspace_a, "enabled": True},
    )
    assert enable_a_resp.status_code == 200, enable_a_resp.text
    enable_b_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/overrides",
        json={"workspace_id": workspace_b, "enabled": True},
    )
    assert enable_b_resp.status_code == 200, enable_b_resp.text

    list_a_enabled = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    assert any(server["id"] == server_id for server in list_a_enabled.json()["inherited"])
    list_b_enabled = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert any(server["id"] == server_id for server in list_b_enabled.json()["inherited"])

    # Disable for workspace_b via override (deletes the row).
    disable_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/overrides",
        json={"workspace_id": workspace_b, "enabled": False},
    )
    assert disable_resp.status_code == 200, disable_resp.text
    # Only workspace_a override remains.
    assert _overrides_by_workspace(disable_resp.json()) == {workspace_a: True}

    list_a_after = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    assert any(server["id"] == server_id for server in list_a_after.json()["inherited"])
    list_b_after = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert all(server["id"] != server_id for server in list_b_after.json()["inherited"])

    # Re-enable for workspace_b creates the override row again.
    reenable_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/overrides",
        json={"workspace_id": workspace_b, "enabled": True},
    )
    assert reenable_resp.status_code == 200, reenable_resp.text
    assert _overrides_by_workspace(reenable_resp.json()) == {
        workspace_a: True,
        workspace_b: True,
    }
    list_b_again = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert any(server["id"] == server_id for server in list_b_again.json()["inherited"])


async def test_workspace_owned_server_rejects_overrides(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "private-no-overrides",
            "server_url": "http://127.0.0.1:9/private-no-overrides",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    override_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/overrides",
        json={"workspace_id": workspace_id, "enabled": False},
    )

    assert override_resp.status_code == 400
    assert override_resp.json()["detail"]["code"] == "mcp_workspace_owned_no_override"
