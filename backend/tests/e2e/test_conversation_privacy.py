"""E2E test: conversations are private to their creator even inside a shared workspace.

Product rule (2026-04): a conversation is only visible to the user who
created it. Two members of the same workspace must not be able to see
each other's conversations via list/get/update/delete/messages.
"""

import secrets

import pytest

from cubebox.api.middleware.rate_limit import limiter

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


async def _seed_csrf(client) -> str:
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get("cubebox_csrf")
    assert csrf, "cubebox_csrf cookie not set after GET /api/v1/auth/me"
    return csrf


async def _login(client, email: str, password: str) -> str:
    client.cookies.clear()
    r = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert r.status_code in (200, 204), r.text
    return await _seed_csrf(client)


@pytest.mark.asyncio
async def test_conversation_invisible_to_other_member_same_workspace(
    unauthenticated_memory_client,
):
    """B (a member of the same workspace) cannot see A's conversation."""
    client = unauthenticated_memory_client

    a_email = f"a-{secrets.token_hex(4)}@example.com"
    b_email = f"b-{secrets.token_hex(4)}@example.com"
    pw = "passwordpassword"

    for email in (a_email, b_email):
        r = await client.post("/api/v1/auth/register", json={"email": email, "password": pw})
        assert r.status_code == 201, r.text

    # --- A: create shared workspace, invite B, create a conversation --------
    csrf_a = await _login(client, a_email, pw)
    # Fetch A's org_id from their auto-created workspace (via on_after_register).
    r = await client.get("/api/v1/workspaces")
    assert r.status_code == 200, r.text
    a_org_id = r.json()[0]["org_id"]
    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "Shared", "org_id": a_org_id},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/invites",
        json={"role": "member"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    invite_token = r.json()["token"]

    r = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "A's private chat"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    conv_id = r.json()["id"]

    # --- B: accept invite (legit workspace member via the HTTP flow) --------
    csrf_b = await _login(client, b_email, pw)
    r = await client.post(
        "/api/v1/workspaces/invites/accept",
        json={"token": invite_token},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 200, r.text

    # B's list must be empty — A's conversation is filtered out by creator_user_id.
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["conversations"] == []

    # Direct read: 404 (structurally invisible, not 403).
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert r.status_code == 404, r.text

    # Update: 404.
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}",
        params={"title": "Hijacked"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # Delete: 404.
    r = await client.delete(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # Messages list: 404 (the conversation-existence pre-check fires first).
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages")
    assert r.status_code == 404, r.text

    # Send message: 404 (same pre-check gate).
    r = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "sneaky"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # --- A positive control: A still sees their own conversation ------------
    csrf_a = await _login(client, a_email, pw)
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "A's private chat"

    r = await client.get(f"/api/v1/ws/{ws_id}/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["conversations"][0]["id"] == conv_id
