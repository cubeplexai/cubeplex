"""E2E: login blocked when email not verified."""

import secrets

import httpx
import pytest
from redis.asyncio import Redis

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _read_otp(redis_client: Redis, email: str) -> str | None:
    """Read the OTP code from Redis."""
    key = f"email_otp:{email}"
    data = await redis_client.hgetall(key)
    if not data:
        return None
    code_bytes = data.get(b"code") or data.get("code")
    if code_bytes is None:
        return None
    if isinstance(code_bytes, bytes):
        return code_bytes.decode()
    return str(code_bytes)


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
async def test_login_blocked_when_unverified(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register without verification -> login returns 403 email_not_verified."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"unverified-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"

    # Register (verification required)
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["verification_required"] is True

    # Attempt login without verifying -> 403 email_not_verified
    await unauthenticated_memory_client.get("/api/v1/auth/me")  # seed CSRF cookie
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
    login_resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert login_resp.status_code == 403, login_resp.text
    detail = login_resp.json()["detail"]
    assert detail["code"] == "email_not_verified"

    # Now verify OTP
    code = await _read_otp(redis_client, email)
    assert code is not None

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 200, resp.text

    # Login now succeeds
    await _login(unauthenticated_memory_client, email, password)

    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["is_verified"] is True
