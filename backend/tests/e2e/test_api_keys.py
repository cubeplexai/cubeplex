"""E2E for personal-access API keys.

Covers the full lifecycle (create → list → use → delete) and the auth
invariants we care about:

- The plaintext token is shown only on create; subsequent lookups expose
  only the prefix.
- A Bearer token authenticates business calls without the cookie session
  AND skips CSRF.
- Revoking a key (DELETE) immediately invalidates Bearer auth.
- 10-key quota is enforced (409 on 11th).
- Cross-user delete is rejected.
"""

import secrets

import httpx
import pytest
import pytest_asyncio

from tests.e2e.conftest import DEFAULT_WS_ID, _auth_cookie_name
from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture(autouse=True)
async def _wipe_default_user_keys(async_client):
    """The default user is shared across tests; keys persist in the DB and the
    quota test (10 keys) would otherwise contaminate a sibling test."""
    for k in (await async_client.get("/api/v1/me/api-keys")).json():
        await async_client.delete(f"/api/v1/me/api-keys/{k['id']}")
    yield
    for k in (await async_client.get("/api/v1/me/api-keys")).json():
        await async_client.delete(f"/api/v1/me/api-keys/{k['id']}")


async def _register_and_login(client: httpx.AsyncClient, email: str, password: str) -> None:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    await client.get("/api/v1/auth/me")
    csrf_name = csrf_cookie_name()
    csrf = client.cookies.get(csrf_name) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_name) or csrf


@pytest.mark.asyncio
async def test_create_then_list_hides_token(async_client):
    r = await async_client.post("/api/v1/me/api-keys", json={"label": "harness"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith("sk-")
    assert len(body["token"]) > len(body["prefix"])
    assert body["prefix"] == body["token"][:12]
    key_id = body["id"]

    r = await async_client.get("/api/v1/me/api-keys")
    assert r.status_code == 200
    items = r.json()
    match = [k for k in items if k["id"] == key_id]
    assert len(match) == 1
    listed = match[0]
    assert "token" not in listed
    assert listed["prefix"] == body["prefix"]
    assert listed["label"] == "harness"


@pytest.mark.asyncio
async def test_bearer_auth_works_without_cookie(async_client):
    """Token must authenticate workspace calls in a fresh cookie-less client."""
    r = await async_client.post("/api/v1/me/api-keys", json={"label": "bench"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    async with httpx.AsyncClient(
        transport=async_client._transport,  # type: ignore[attr-defined]
        base_url=async_client.base_url,
        headers={"Authorization": f"Bearer {token}"},
    ) as fresh:
        # No cookies, no CSRF — only Bearer. Hit a mutating endpoint to
        # confirm CSRF was bypassed.
        r = await fresh.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            json={"title": "via-bearer", "draft": True},
        )
        assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_delete_revokes_bearer_auth(async_client):
    r = await async_client.post("/api/v1/me/api-keys", json={"label": "tmp"})
    token = r.json()["token"]
    key_id = r.json()["id"]

    async with httpx.AsyncClient(
        transport=async_client._transport,  # type: ignore[attr-defined]
        base_url=async_client.base_url,
        headers={"Authorization": f"Bearer {token}"},
    ) as fresh:
        # Confirm it works first.
        r = await fresh.get("/api/v1/auth/me")
        assert r.status_code == 200

    r = await async_client.delete(f"/api/v1/me/api-keys/{key_id}")
    assert r.status_code == 204

    async with httpx.AsyncClient(
        transport=async_client._transport,  # type: ignore[attr-defined]
        base_url=async_client.base_url,
        headers={"Authorization": f"Bearer {token}"},
    ) as fresh:
        r = await fresh.get("/api/v1/auth/me")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_quota_blocks_eleventh_key(async_client):
    for i in range(10):
        r = await async_client.post("/api/v1/me/api-keys", json={"label": f"k{i}"})
        assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/me/api-keys", json={"label": "overflow"})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_cannot_delete_other_users_key(async_client, unauthenticated_memory_client):
    """A second user must not be able to delete the first user's key."""
    r = await async_client.post("/api/v1/me/api-keys", json={"label": "owned-by-A"})
    key_id = r.json()["id"]

    other_email = f"u-{secrets.token_hex(4)}@example.com"
    await _register_and_login(unauthenticated_memory_client, other_email, "correctbattery1")
    r = await unauthenticated_memory_client.delete(f"/api/v1/me/api-keys/{key_id}")
    assert r.status_code == 404

    # And the original owner can still delete it.
    r = await async_client.delete(f"/api/v1/me/api-keys/{key_id}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list(unauthenticated_memory_client):
    r = await unauthenticated_memory_client.get("/api/v1/me/api-keys")
    assert r.status_code == 401
    assert _auth_cookie_name() not in unauthenticated_memory_client.cookies
