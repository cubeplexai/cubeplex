"""E2E tests for the MCP OAuth placeholder behavior."""

import httpx


async def test_admin_oauth_create_and_test_connection_return_placeholder(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client
    server_name = "oauth-admin-placeholder"

    create_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": server_name,
            "server_url": "http://127.0.0.1:9/oauth-admin",
            "transport": "streamable_http",
            "auth_method": "oauth",
            "credential_scope": "org",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 409
    assert create_resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"

    test_resp = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "http://127.0.0.1:9/oauth-admin-test",
            "transport": "streamable_http",
            "auth_method": "oauth",
            "credential_scope": "org",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert test_resp.status_code == 409
    assert test_resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"

    list_resp = await client.get("/api/v1/admin/mcp/servers")
    assert list_resp.status_code == 200
    assert all(server["name"] != server_name for server in list_resp.json())


async def test_workspace_oauth_create_and_test_connection_return_placeholder(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client
    server_name = "oauth-workspace-placeholder"

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": server_name,
            "server_url": "http://127.0.0.1:9/oauth-workspace",
            "transport": "streamable_http",
            "auth_method": "oauth",
            "credential_scope": "workspace",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 409
    assert create_resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"

    test_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/test-connection",
        json={
            "server_url": "http://127.0.0.1:9/oauth-workspace-test",
            "transport": "streamable_http",
            "auth_method": "oauth",
            "credential_scope": "workspace",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert test_resp.status_code == 409
    assert test_resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"

    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    assert list_resp.status_code == 200
    assert all(server["name"] != server_name for server in list_resp.json()["owned"])
