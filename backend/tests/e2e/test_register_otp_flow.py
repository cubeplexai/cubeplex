"""E2E: register with email OTP verification enabled."""

import secrets

import httpx
import pytest
from redis.asyncio import Redis

from tests.e2e.conftest import _auth_cookie_name
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


async def _seed_csrf(client: httpx.AsyncClient) -> str:
    """Seed the CSRF cookie via a safe GET and return the token."""
    resp = await client.get("/api/v1/auth/me")
    # 200 if authenticated, 401 if not — either way the CSRF cookie is set.
    assert resp.status_code in (200, 401), resp.text
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    client.headers["X-CSRF-Token"] = csrf
    return csrf


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    """Log in on a fresh client; sets auth + CSRF cookies."""
    await client.get("/api/v1/auth/me")  # seed CSRF cookie
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


@pytest.mark.asyncio
async def test_register_otp_flow(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy path: register -> verify-otp -> logged-in -> me has is_verified + needs_onboarding."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"otp-happy-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"

    # Register
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["verification_required"] is True
    # Register does NOT set auth cookie
    assert unauthenticated_memory_client.cookies.get(_auth_cookie_name()) is None

    # Read OTP from Redis
    code = await _read_otp(redis_client, email)
    assert code is not None, f"OTP not found in Redis for {email}"

    # Verify OTP — sets auth cookie on success
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert unauthenticated_memory_client.cookies.get(_auth_cookie_name()) is not None

    # GET /me should show is_verified + needs_onboarding
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    me_data = me.json()
    assert me_data["is_verified"] is True
    assert me_data["needs_onboarding"] is True
    assert me_data["email"] == email


@pytest.mark.asyncio
async def test_verify_otp_replay_rejected(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second verify with same code returns 400 (key deleted on first success)."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"otp-replay-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    code = await _read_otp(redis_client, email)
    assert code is not None

    # First verify succeeds and sets auth cookie
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 200

    # Seed CSRF cookie (auth cookie is now present, so CSRF enforcement kicks in)
    await _seed_csrf(unauthenticated_memory_client)

    # Second verify with same code fails (key deleted)
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] in ("otp_expired", "invalid_otp")


@pytest.mark.asyncio
async def test_verify_otp_wrong_code(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong OTP code returns 400 with remaining_attempts."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"otp-wrong-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": "000000"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_otp"
    assert detail["remaining_attempts"] == 4


@pytest.mark.asyncio
async def test_verify_otp_max_attempts(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 wrong attempts exhausts OTP, returns otp_max_attempts and key is deleted."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"otp-max-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    for attempt in range(5):
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/verify-otp",
            json={"email": email, "code": "000000"},
        )
        if attempt < 4:
            assert resp.status_code == 400, f"attempt {attempt}: {resp.text}"
            detail = resp.json()["detail"]
            assert detail["code"] == "invalid_otp"
        else:
            assert resp.status_code == 400, resp.text
            detail = resp.json()["detail"]
            assert detail["code"] == "otp_max_attempts"

    # Key should be deleted from Redis
    code = await _read_otp(redis_client, email)
    assert code is None, "OTP key should have been deleted after max attempts"


@pytest.mark.asyncio
async def test_resend_otp_cooldown(
    unauthenticated_memory_client: httpx.AsyncClient,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resend within cooldown returns 429 otp_cooldown."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    email = f"otp-cooldown-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    # First resend (within cooldown because register already issued one)
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/resend-otp",
        json={"email": email},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"]["code"] == "otp_cooldown"


@pytest.mark.asyncio
async def test_resend_otp_unknown_email(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resend for non-existent email returns 200 (no email enumeration)."""
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: True)
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/resend-otp",
        json={"email": "nonexistent@example.com"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
