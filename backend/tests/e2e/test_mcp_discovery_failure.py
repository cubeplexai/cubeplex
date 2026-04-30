"""E2E tests for MCP discovery failures."""

import httpx


async def test_unreachable_mcp_server_records_discovery_failure(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client

    create_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "unreachable-discovery",
            "server_url": "http://127.0.0.1:9/unreachable-discovery",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    server_id = created["id"]
    assert created["authed"] is False
    assert created["tools_cache"] == []
    assert created["last_error"]
    assert created["last_discovered_at"] is not None

    refresh_resp = await client.post(f"/api/v1/admin/mcp/servers/{server_id}/refresh-tools")
    assert refresh_resp.status_code == 200, refresh_resp.text
    refreshed = refresh_resp.json()
    assert refreshed["authed"] is False
    assert refreshed["tools_cache"] == []
    assert refreshed["last_error"]
    assert refreshed["last_discovered_at"] is not None

    list_resp = await client.get("/api/v1/admin/mcp/servers", params={"has_error": True})
    assert list_resp.status_code == 200
    assert any(server["id"] == server_id for server in list_resp.json())


async def test_connection_failure_dry_run_does_not_persist_server(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client
    server_name = "dry-run-unreachable"

    before_resp = await client.get("/api/v1/admin/mcp/servers")
    assert before_resp.status_code == 200
    before_ids = {server["id"] for server in before_resp.json()}

    test_resp = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "http://127.0.0.1:9/dry-run-unreachable",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert test_resp.status_code == 200, test_resp.text
    body = test_resp.json()
    assert body["success"] is False
    assert body["tools"] is None
    assert body["error"]

    after_resp = await client.get("/api/v1/admin/mcp/servers")
    assert after_resp.status_code == 200
    after_servers = after_resp.json()
    assert {server["id"] for server in after_servers} == before_ids
    assert all(server["name"] != server_name for server in after_servers)
