"""E2E tests for admin-managed MCP workspace bindings."""

import httpx


async def _create_second_workspace(client: httpx.AsyncClient, workspace_id: str) -> str:
    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    current = next(ws for ws in workspaces_resp.json() if ws["id"] == workspace_id)

    create_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "MCP Bindings Secondary", "org_id": current["org_id"]},
    )
    assert create_resp.status_code == 201, create_resp.text
    return create_resp.json()["id"]


def _bindings_by_workspace(bindings: list[dict[str, object]]) -> dict[str, bool]:
    return {str(binding["workspace_id"]): bool(binding["enabled"]) for binding in bindings}


async def test_admin_bindings_control_workspace_visibility(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_a = admin_client
    workspace_b = await _create_second_workspace(client, workspace_a)

    create_resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "bindings-org-wide",
            "server_url": "http://127.0.0.1:9/bindings-org-wide",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    bind_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/bindings",
        json={
            "bindings": [
                {"workspace_id": workspace_a, "enabled": True},
                {"workspace_id": workspace_b, "enabled": False},
            ]
        },
    )
    assert bind_resp.status_code == 200, bind_resp.text
    assert _bindings_by_workspace(bind_resp.json()) == {
        workspace_a: True,
        workspace_b: False,
    }

    list_a_resp = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    assert list_a_resp.status_code == 200
    assert any(server["id"] == server_id for server in list_a_resp.json()["via_binding"])

    list_b_resp = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert list_b_resp.status_code == 200
    assert all(server["id"] != server_id for server in list_b_resp.json()["via_binding"])

    replace_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/bindings",
        json={"bindings": [{"workspace_id": workspace_b, "enabled": True}]},
    )
    assert replace_resp.status_code == 200, replace_resp.text
    assert _bindings_by_workspace(replace_resp.json()) == {workspace_b: True}

    list_a_after_replace_resp = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    assert list_a_after_replace_resp.status_code == 200
    assert all(
        server["id"] != server_id for server in list_a_after_replace_resp.json()["via_binding"]
    )

    list_b_after_replace_resp = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    assert list_b_after_replace_resp.status_code == 200
    assert any(
        server["id"] == server_id for server in list_b_after_replace_resp.json()["via_binding"]
    )


async def test_workspace_owned_server_rejects_admin_bindings(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "private-no-bindings",
            "server_url": "http://127.0.0.1:9/private-no-bindings",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    bind_resp = await client.put(
        f"/api/v1/admin/mcp/servers/{server_id}/bindings",
        json={"bindings": [{"workspace_id": workspace_id, "enabled": True}]},
    )

    assert bind_resp.status_code == 400
    assert bind_resp.json()["detail"]["code"] == "mcp_workspace_owned_no_binding"
