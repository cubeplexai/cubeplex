"""E2E tests for admin MCP CRUD routes."""

import httpx


async def test_admin_mcp_server_crud(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client

    create_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "admin-crud-mcp",
            "server_url": "http://127.0.0.1:9/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "headers": {"X-Test": "admin"},
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    server_id = created["id"]
    assert created["name"] == "admin-crud-mcp"
    assert created["credential_scope"] == "none"
    assert created["owner_workspace_id"] is None

    list_resp = await client.get("/api/v1/admin/mcp/servers")
    assert list_resp.status_code == 200
    assert any(server["id"] == server_id for server in list_resp.json())

    detail_resp = await client.get(f"/api/v1/admin/mcp/servers/{server_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == server_id

    patch_resp = await client.patch(
        f"/api/v1/admin/mcp/servers/{server_id}",
        json={"name": "admin-crud-mcp-renamed", "headers": {"X-Test": "updated"}},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["name"] == "admin-crud-mcp-renamed"
    assert patch_resp.json()["headers"] == {"X-Test": "updated"}

    delete_resp = await client.delete(f"/api/v1/admin/mcp/servers/{server_id}")
    assert delete_resp.status_code == 204

    missing_resp = await client.get(f"/api/v1/admin/mcp/servers/{server_id}")
    assert missing_resp.status_code == 404
    assert missing_resp.json()["detail"]["code"] == "mcp_server_not_found"
