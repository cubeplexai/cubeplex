"""E2E test: ScopedRepository structurally prevents cross-workspace data leaks."""

import secrets

import pytest

from cubeplex.api.middleware.rate_limit import limiter
from tests.e2e.conftest import _auth_cookie_name
from tests.e2e.helpers import csrf_cookie_name as _csrf_cookie_name

pytestmark = pytest.mark.e2e


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
    `cubeplex_csrf` cookie lands in the client jar. The value is then used
    in the `X-CSRF-Token` header on subsequent mutating requests.
    """
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(_csrf_cookie_name())
    assert csrf, "cubeplex_csrf cookie not set after GET /api/v1/auth/me"
    return csrf


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

    # --- User A: login, complete onboarding, create conversation -----------
    r = await client.post("/api/v1/auth/login", data={"username": a_email, "password": pw})
    assert r.status_code in (200, 204), r.text
    assert _auth_cookie_name() in client.cookies

    csrf_a = await _seed_csrf(client)
    a_slug = f"scope-a-{secrets.token_hex(4)}"
    r = await client.post(
        "/api/v1/onboarding",
        json={"org_name": "A's org", "org_slug": a_slug, "workspace_name": "A's ws"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    ws_a = r.json()["workspace_id"]

    r = await client.post(
        f"/api/v1/ws/{ws_a}/conversations",
        params={"title": "Secret"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    conv_id = r.json()["id"]

    # Positive control: A must be able to GET their own conversation in ws_a.
    # Without this, a silent bug (e.g. creation landing in the wrong workspace,
    # or the endpoint returning a stub id without persisting) would make B's
    # 404 pass for the wrong reason.
    r = await client.get(f"/api/v1/ws/{ws_a}/conversations/{conv_id}")
    assert r.status_code == 200, f"A must see their own conversation: {r.text}"

    # A logs out.
    r = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_a})
    assert r.status_code == 204, r.text

    # Clear the cookie jar so B starts from a clean slate. Logout clears the
    # auth cookie but not necessarily the CSRF cookie; if the server doesn't
    # rotate CSRF on B's login, _seed_csrf could otherwise return A's stale
    # token and mask a CSRF-rotation bug.
    client.cookies.clear()

    # --- User B: login, complete onboarding, try to read A's conversation ---
    r = await client.post("/api/v1/auth/login", data={"username": b_email, "password": pw})
    assert r.status_code in (200, 204), r.text

    csrf_b = await _seed_csrf(client)
    b_slug = f"scope-b-{secrets.token_hex(4)}"
    r = await client.post(
        "/api/v1/onboarding",
        json={"org_name": "B's org", "org_slug": b_slug, "workspace_name": "B's ws"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 201, r.text
    ws_b = r.json()["workspace_id"]

    # Direct read must 404 — structurally invisible, not a 403 auth error.
    # 404 because ScopedRepository filters by (org_id, workspace_id) at the
    # query layer — the row is invisible, not merely forbidden.
    r = await client.get(f"/api/v1/ws/{ws_b}/conversations/{conv_id}")
    assert r.status_code == 404, r.text

    # List must be empty — the scoped WHERE clause hides A's row entirely.
    r = await client.get(f"/api/v1/ws/{ws_b}/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["conversations"] == []
