"""E2E test: ScopedRepository structurally prevents cross-workspace data leaks."""

import secrets

import pytest

from cubebox.api.middleware.rate_limit import limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the shared slowapi limiter between tests.

    Without this, register/login rate limits (3/min, 5/min) accumulate across
    the test run and cause spurious 429s — all requests share the same
    ASGI-transport remote address.
    """
    limiter.reset()
    yield
    limiter.reset()


async def _seed_csrf(client) -> str:
    """Ensure the CSRF cookie is set on the client and return its value.

    After login, a safe GET to `/api/v1/auth/me` is enough to guarantee the
    `cubebox_csrf` cookie lands in the client jar. The value is then used
    in the `X-CSRF-Token` header on subsequent mutating requests.
    """
    await client.get("/api/v1/auth/me")
    return client.cookies.get("cubebox_csrf") or ""


@pytest.mark.asyncio
async def test_conversation_invisible_to_other_workspace(unauthenticated_memory_client):
    """User A's conversation is structurally invisible to user B in a different workspace.

    ScopedRepository applies org_id + workspace_id predicates on every query;
    cross-workspace reads therefore look like the row simply does not exist
    (404), not a permission error (403). User B's list should be empty.
    """
    client = unauthenticated_memory_client

    a_email = f"a-{secrets.token_hex(4)}@example.com"
    b_email = f"b-{secrets.token_hex(4)}@example.com"
    pw = "passwordpassword"

    # Register both users. Register does not require CSRF (no auth cookie yet).
    r = await client.post("/api/v1/auth/register", json={"email": a_email, "password": pw})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/register", json={"email": b_email, "password": pw})
    assert r.status_code == 201, r.text

    # --- User A: login, create workspace W_A, create conversation -----------
    r = await client.post("/api/v1/auth/login", data={"username": a_email, "password": pw})
    assert r.status_code in (200, 204), r.text
    assert "cubebox_auth" in client.cookies

    csrf_a = await _seed_csrf(client)
    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "A's ws", "org_id": "default-org"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    ws_a = r.json()["id"]

    r = await client.post(
        "/api/v1/conversations",
        params={"title": "Secret"},
        headers={"X-CSRF-Token": csrf_a, "X-Workspace-Id": ws_a},
    )
    assert r.status_code == 201, r.text
    conv_id = r.json()["id"]

    # A logs out.
    r = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_a})
    assert r.status_code == 204, r.text

    # --- User B: login, create workspace W_B, try to read A's conversation ---
    r = await client.post("/api/v1/auth/login", data={"username": b_email, "password": pw})
    assert r.status_code in (200, 204), r.text

    csrf_b = await _seed_csrf(client)
    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "B's ws", "org_id": "default-org"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 201, r.text
    ws_b = r.json()["id"]
    headers_b = {"X-CSRF-Token": csrf_b, "X-Workspace-Id": ws_b}

    # Direct read must 404 — structurally invisible, not a 403 auth error.
    r = await client.get(f"/api/v1/conversations/{conv_id}", headers=headers_b)
    assert r.status_code == 404, r.text

    # List must be empty — the scoped WHERE clause hides A's row entirely.
    r = await client.get("/api/v1/conversations", headers=headers_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["conversations"] == []
