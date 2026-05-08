"""E2E tests for promoting workspace MCP servers to org-wide connectors."""

import httpx


async def test_promote_alpha_shares_workspace_credential_to_org(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "promote-alpha",
            "server_url": "http://127.0.0.1:9/promote-alpha",
            "transport": "streamable_http",
            "auth_method": "static",
            "credential_scope": "workspace",
            "credential_plaintext": "alpha-ws-token",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    promote_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/promote-to-org",
        json={"share_credential": True},
    )

    assert promote_resp.status_code == 200, promote_resp.text
    promoted = promote_resp.json()
    assert promoted["owner_workspace_id"] is None
    assert promoted["credential_scope"] == "org"
    assert promoted["credential"] is not None

    status_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/workspace-credential"
    )
    assert status_resp.status_code == 200
    assert status_resp.json() == {"has_value": False}

    # Promotion no longer creates an explicit binding/override row — the
    # source workspace inherits the new org-wide install by default.
    overrides_resp = await client.get(f"/api/v1/admin/mcp/servers/{server_id}/overrides")
    assert overrides_resp.status_code == 200, overrides_resp.text
    assert overrides_resp.json() == []

    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert any(server["id"] == server_id for server in body["inherited"])
    assert all(server["id"] != server_id for server in body["owned"])


async def test_promote_beta_keeps_workspace_credential_private(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "promote-beta",
            "server_url": "http://127.0.0.1:9/promote-beta",
            "transport": "streamable_http",
            "auth_method": "static",
            "credential_scope": "workspace",
            "credential_plaintext": "beta-ws-token",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    promote_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/promote-to-org",
        json={"share_credential": False},
    )

    assert promote_resp.status_code == 200, promote_resp.text
    promoted = promote_resp.json()
    assert promoted["owner_workspace_id"] is None
    assert promoted["credential_scope"] == "workspace"
    assert promoted["credential"] is None

    status_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/workspace-credential"
    )
    assert status_resp.status_code == 200
    assert status_resp.json() == {"has_value": True}

    overrides_resp = await client.get(f"/api/v1/admin/mcp/servers/{server_id}/overrides")
    assert overrides_resp.status_code == 200, overrides_resp.text
    assert overrides_resp.json() == []
