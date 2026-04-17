"""E2E auth tests: register, login, logout, duplicate email, me-requires-auth."""

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


@pytest.mark.asyncio
async def test_register_and_login_sets_cookie(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    assert r.status_code == 204
    assert "cubebox_auth" in unauthenticated_memory_client.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_fails(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "right-password-1"}
    )
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": "wrong-password"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_email_fails(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201
    r2 = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_logout_clears_cookie(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    # Seed CSRF cookie via a safe GET (logout is a mutating request on an
    # authenticated session, so CSRF middleware requires the double-submit token).
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get("cubebox_csrf") or ""
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 204
    r2 = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(unauthenticated_memory_client):
    r = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert r.status_code == 401
