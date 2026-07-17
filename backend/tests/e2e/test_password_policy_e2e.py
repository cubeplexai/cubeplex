"""E2E: password policy enforcement at register and change-password."""

import secrets

import httpx
import pytest

from cubeplex.auth.password_policy import PasswordPolicy
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


async def _seed_csrf(client: httpx.AsyncClient) -> str:
    """Seed the CSRF cookie via a safe GET and return the token."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code in (200, 401), resp.text
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    client.headers["X-CSRF-Token"] = csrf
    return csrf


@pytest.mark.asyncio
async def test_register_high_policy_weak_password(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH policy: short password returns 400 weak_password."""
    monkeypatch.setattr(
        "cubeplex.auth.password_policy.get_password_policy",
        lambda: PasswordPolicy.HIGH,
    )
    email = f"high-weak-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "short"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "weak_password"
    assert "password_too_short" in detail["errors"]


@pytest.mark.asyncio
async def test_register_high_policy_no_symbol(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH policy: password without symbol returns 400."""
    monkeypatch.setattr(
        "cubeplex.auth.password_policy.get_password_policy",
        lambda: PasswordPolicy.HIGH,
    )
    email = f"high-nosym-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "NoSymbol1"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "weak_password"
    assert "password_no_symbol" in detail["errors"]


@pytest.mark.asyncio
async def test_register_high_policy_strong(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH policy: strong password succeeds."""
    monkeypatch.setattr(
        "cubeplex.auth.password_policy.get_password_policy",
        lambda: PasswordPolicy.HIGH,
    )
    email = f"high-strong-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Str0ng!Pass"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_register_low_policy_weak(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOW policy: 8-digit password succeeds (only length checked)."""
    monkeypatch.setattr(
        "cubeplex.auth.password_policy.get_password_policy",
        lambda: PasswordPolicy.LOW,
    )
    email = f"low-weak-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "12345678"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_change_password_high_policy(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Change-password enforces policy: weak fails, strong succeeds."""
    monkeypatch.setattr(
        "cubeplex.auth.password_policy.get_password_policy",
        lambda: PasswordPolicy.HIGH,
    )
    email = f"changepw-{secrets.token_hex(4)}@example.com"
    password = "Initial!Pass1"

    # Register and login (verification disabled by default)
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text

    await _login(unauthenticated_memory_client, email, password)

    # Try change-password to a weak password
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password, "new_password": "short"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "weak_password"
    assert "password_too_short" in detail["errors"]

    # Try change-password to a strong password
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password, "new_password": "NewStr0ng!Pass"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
