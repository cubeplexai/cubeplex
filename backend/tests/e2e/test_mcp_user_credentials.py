"""E2E tests for per-user MCP credentials in a shared workspace."""

import secrets

import httpx


async def _seed_csrf(client: httpx.AsyncClient) -> str:
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get("cubebox_csrf")
    assert csrf is not None
    return csrf


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    client.cookies.clear()
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code in (200, 204), resp.text
    return await _seed_csrf(client)


async def test_user_scope_credentials_are_isolated_per_workspace_member(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    client = unauthenticated_memory_client
    password = "passwordpassword"
    run_id = secrets.token_hex(4)
    alice_email = f"mcp-alice-{run_id}@example.com"
    bob_email = f"mcp-bob-{run_id}@example.com"

    for email in (alice_email, bob_email):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201, resp.text

    csrf_alice = await _login(client, alice_email, password)
    # Fetch Alice's org_id from her auto-created workspace (via on_after_register).
    ws_list_resp = await client.get("/api/v1/workspaces")
    assert ws_list_resp.status_code == 200, ws_list_resp.text
    alice_org_id = ws_list_resp.json()[0]["org_id"]
    workspace_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "MCP Shared", "org_id": alice_org_id},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert workspace_resp.status_code == 201, workspace_resp.text
    workspace_id = workspace_resp.json()["id"]

    invite_resp = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invite_token = invite_resp.json()["token"]

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "per-user-mcp",
            "server_url": "http://127.0.0.1:9/per-user",
            "transport": "streamable_http",
            "auth_method": "static",
            "credential_scope": "user",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    alice_status_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential"
    )
    assert alice_status_resp.status_code == 200
    assert alice_status_resp.json() == {"has_value": False}

    alice_put_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential",
        json={"plaintext": "alice-token", "name": f"alice-{run_id}"},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert alice_put_resp.status_code == 200, alice_put_resp.text
    assert alice_put_resp.json() == {"has_value": True}

    csrf_bob = await _login(client, bob_email, password)
    accept_resp = await client.post(
        "/api/v1/workspaces/invites/accept",
        json={"token": invite_token},
        headers={"X-CSRF-Token": csrf_bob},
    )
    assert accept_resp.status_code == 200, accept_resp.text

    bob_status_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential"
    )
    assert bob_status_resp.status_code == 200
    assert bob_status_resp.json() == {"has_value": False}

    bob_put_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential",
        json={"plaintext": "bob-token", "name": f"bob-{run_id}"},
        headers={"X-CSRF-Token": csrf_bob},
    )
    assert bob_put_resp.status_code == 200, bob_put_resp.text
    assert bob_put_resp.json() == {"has_value": True}

    delete_bob_resp = await client.delete(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential",
        headers={"X-CSRF-Token": csrf_bob},
    )
    assert delete_bob_resp.status_code == 204, delete_bob_resp.text

    bob_status_after_delete_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential"
    )
    assert bob_status_after_delete_resp.status_code == 200
    assert bob_status_after_delete_resp.json() == {"has_value": False}

    await _login(client, alice_email, password)
    alice_status_after_bob_delete_resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential"
    )
    assert alice_status_after_bob_delete_resp.status_code == 200
    assert alice_status_after_bob_delete_resp.json() == {"has_value": True}
