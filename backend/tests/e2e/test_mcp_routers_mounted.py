"""Smoke tests for mounted MCP routers."""

import httpx


async def test_admin_servers_list_requires_auth(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    resp = await unauthenticated_memory_client.get("/api/v1/admin/mcp/servers")

    assert resp.status_code == 401


async def test_ws_servers_list_requires_auth(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    resp = await unauthenticated_memory_client.get("/api/v1/ws/some-ws/mcp/servers")

    assert resp.status_code == 401


async def test_admin_endpoint_404_for_unknown_server(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client

    resp = await client.get("/api/v1/admin/mcp/servers/nonexistent-id")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "mcp_server_not_found"
