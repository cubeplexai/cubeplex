"""E2E auth tests: register, login, logout, duplicate email, me-requires-auth."""

import secrets

import pytest

from cubeplex.api.middleware.rate_limit import limiter
from tests.e2e.conftest import _auth_cookie_name
from tests.e2e.helpers import csrf_cookie_name

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
    assert _auth_cookie_name() in unauthenticated_memory_client.cookies


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
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
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


@pytest.mark.asyncio
async def test_me_returns_default_language(unauthenticated_memory_client):
    """Test that GET /auth/me returns language field with default value."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Fresh user should have default language "en"
    resp = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"


@pytest.mark.asyncio
async def test_patch_me_updates_language(unauthenticated_memory_client):
    """Test that PATCH /auth/me updates and persists language."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Seed CSRF cookie via a safe GET.
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""

    # Update language
    resp = await unauthenticated_memory_client.patch(
        "/api/v1/auth/me",
        json={"language": "zh"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.json()["language"] == "zh"

    # Verify persisted
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.json()["language"] == "zh"


@pytest.mark.asyncio
async def test_patch_me_rejects_invalid_language(unauthenticated_memory_client):
    """Test that PATCH /auth/me rejects invalid language values."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Seed CSRF cookie via a safe GET.
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""

    # Attempt to set invalid language
    resp = await unauthenticated_memory_client.patch(
        "/api/v1/auth/me",
        json={"language": "ja"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422  # Pydantic Literal validation


@pytest.mark.asyncio
async def test_login_error_is_localized_zh(
    unauthenticated_memory_client,
) -> None:
    """Login error is localized to Chinese when Accept-Language is zh."""
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
        headers={"Accept-Language": "zh-CN,zh;q=0.9"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "邮箱或密码错误"


@pytest.mark.asyncio
async def test_login_error_is_localized_en(
    unauthenticated_memory_client,
) -> None:
    """Login error is in English when Accept-Language is en."""
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid email or password"
