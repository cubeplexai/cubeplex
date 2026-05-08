"""E2E tests for workspace MCP self-service routes."""

import httpx


async def test_member_creates_workspace_scope_server(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = member_client

    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "member-workspace-mcp",
            "server_url": "http://127.0.0.1:9/member-workspace",
            "transport": "streamable_http",
            "auth_method": "static",
            "credential_scope": "workspace",
            "credential_plaintext": "ws-token",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["owner_workspace_id"] == workspace_id
    assert body["credential_scope"] == "workspace"
    assert body["credential"] is None


async def test_member_cannot_create_org_scope_via_workspace_path(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = member_client

    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "bad-org-scope",
            "server_url": "http://127.0.0.1:9/bad-org-scope",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "org",
        },
    )

    assert resp.status_code == 422


async def test_other_workspace_member_cannot_edit_owned_server(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    client_a, workspace_id_a = member_client_org_a
    client_b, workspace_id_b = member_client_org_b

    create_resp = await client_a.post(
        f"/api/v1/ws/{workspace_id_a}/mcp/servers",
        json={
            "name": "private-member-mcp",
            "server_url": "http://127.0.0.1:9/private-member",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    resp = await client_b.patch(
        f"/api/v1/ws/{workspace_id_b}/mcp/servers/{server_id}",
        json={"name": "stolen"},
    )

    assert resp.status_code in (403, 404)


async def test_list_returns_owned_and_inherited(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Org-wide installs are inherited by every workspace by default."""
    client, workspace_id = admin_client

    admin_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "org-wide-inherited-mcp",
            "server_url": "http://127.0.0.1:9/org-wide-inherited",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert admin_resp.status_code == 201, admin_resp.text
    server_id = admin_resp.json()["id"]

    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert any(server["id"] == server_id for server in body["inherited"])
    assert all(server["id"] != server_id for server in body["owned"])
