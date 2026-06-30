"""E2E: register with email verification disabled (smtp-disabled / default config)."""

import secrets

import httpx
import pytest

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    """Log in on a fresh client; sets auth + CSRF cookies."""
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


@pytest.mark.asyncio
async def test_register_no_verification_needed(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    """Default config (email.backend=log): register returns verification_required: false."""
    email = f"smtp-off-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["verification_required"] is False

    # Login works without OTP step
    await _login(unauthenticated_memory_client, email, password)

    # GET /me shows is_verified true (no verification needed)
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["is_verified"] is True
