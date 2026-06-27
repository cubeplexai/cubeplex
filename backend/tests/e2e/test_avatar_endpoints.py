import secrets

import pytest

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client, email: str, password: str) -> str:
    """Login and return CSRF token. Mirrors test_single_tenant_register.py."""
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    return client.cookies.get(csrf_cookie_name()) or csrf


async def test_put_avatar_uploaded(fresh_db_unauth_client_single_tenant):
    client = fresh_db_unauth_client_single_tenant
    email = f"av-{secrets.token_hex(4)}@example.com"
    password = "password123"

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    csrf = await _login(client, email, password)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = await client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", png, "image/png")},
        data={"kind": "uploaded"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, f"PUT failed: {r.text}"
    body = r.json()
    assert body["avatar_kind"] == "uploaded"
    assert body["avatar_url"].endswith(".png")


async def test_delete_avatar_reverts(fresh_db_unauth_client_single_tenant):
    """After PUT uploaded, DELETE clears url and sets kind=generated."""
    client = fresh_db_unauth_client_single_tenant
    email = f"av-del-{secrets.token_hex(4)}@example.com"
    password = "password123"

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    csrf = await _login(client, email, password)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    await client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", png, "image/png")},
        data={"kind": "uploaded"},
        headers={"X-CSRF-Token": csrf},
    )

    r = await client.delete(
        "/api/v1/auth/me/avatar",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, f"DELETE failed: {r.text}"
    body = r.json()
    assert body["avatar_kind"] == "generated"
    assert body["avatar_url"] is None


async def test_put_avatar_generated_stores_seed(fresh_db_unauth_client_single_tenant):
    """data kind=generated, seed='abc', style='notionists'."""
    client = fresh_db_unauth_client_single_tenant
    email = f"av-gen-{secrets.token_hex(4)}@example.com"
    password = "password123"

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    csrf = await _login(client, email, password)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = await client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", png, "image/png")},
        data={"kind": "generated", "seed": "abc", "style": "notionists"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, f"PUT generated failed: {r.text}"
    body = r.json()
    assert body["avatar_kind"] == "generated"
    assert body["avatar_seed"] == "abc"
    assert body["avatar_style"] == "notionists"


async def test_cannot_mutate_other_users_avatar(fresh_db_unauth_client_single_tenant):
    """Endpoint is self-scoped: only operates on current_active_user."""
    client = fresh_db_unauth_client_single_tenant
    email = f"av-a-{secrets.token_hex(4)}@example.com"
    password = "password123"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    csrf = await _login(client, email, password)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = await client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", png, "image/png")},
        data={"kind": "uploaded"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["avatar_url"].endswith(".png")
    assert body["avatar_kind"] == "uploaded"
