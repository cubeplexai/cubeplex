"""E2E: org-invite + workspace-invite onboarding flows."""

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
async def test_org_invite_role_owner_rejected(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-invite with role=owner returns 400 role_not_assignable."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    email = f"admin-{secrets.token_hex(4)}@example.com"
    slug = f"admin-{secrets.token_hex(4)}"

    # Register + onboard to become org admin
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"org_name": "AdminOrg", "org_slug": slug, "workspace_name": "AdminWS"},
    )
    assert resp.status_code == 201, resp.text

    # Try creating an invite with role=owner
    resp = await unauthenticated_memory_client.post(
        "/api/v1/admin/orgs/invites",
        json={"role": "owner"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "role_not_assignable"

    # role=member succeeds
    resp = await unauthenticated_memory_client.post(
        "/api/v1/admin/orgs/invites",
        json={"role": "member"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "token" in data
    assert data["role"] == "member"


@pytest.mark.asyncio
async def test_org_invite_accept_then_onboarding(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-invite accept -> needs_onboarding true -> workspace-only onboarding -> done."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)

    # ---- Admin user: register + onboard ----
    admin_email = f"admin2-{secrets.token_hex(4)}@example.com"
    admin_slug = f"admin2-{secrets.token_hex(4)}"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": admin_email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, admin_email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={
            "org_name": "AdminOrg2",
            "org_slug": admin_slug,
            "workspace_name": "AdminWS2",
        },
    )
    assert resp.status_code == 201, resp.text

    # Admin creates an org invite
    resp = await unauthenticated_memory_client.post(
        "/api/v1/admin/orgs/invites",
        json={"role": "member"},
    )
    assert resp.status_code == 201, resp.text
    org_invite_token = resp.json()["token"]

    # ---- Second user: register, accept invite ----
    user2_email = f"invitee-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": user2_email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, user2_email, "StrongPass1!")

    # Accept org invite
    resp = await unauthenticated_memory_client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": org_invite_token},
    )
    assert resp.status_code == 200, resp.text
    accept_data = resp.json()
    assert accept_data["org_id"]
    assert accept_data["role"] == "member"

    # GET /me -> needs_onboarding true (has org, no workspace)
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is True
    assert len(me.json()["org_memberships"]) > 0

    # Workspace-only onboarding
    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"workspace_name": "My WS"},
    )
    assert resp.status_code == 201, resp.text

    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is False


@pytest.mark.asyncio
async def test_org_invite_reuse_rejected(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing an org-invite token returns 400 invite_invalid_or_expired."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)

    # ---- Admin: register + onboard ----
    admin_email = f"reuse-admin-{secrets.token_hex(4)}@example.com"
    admin_slug = f"reuse-admin-{secrets.token_hex(4)}"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": admin_email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, admin_email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={
            "org_name": "ReuseOrg",
            "org_slug": admin_slug,
            "workspace_name": "ReuseWS",
        },
    )
    assert resp.status_code == 201, resp.text

    # Create invite
    resp = await unauthenticated_memory_client.post(
        "/api/v1/admin/orgs/invites",
        json={"role": "member"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]

    # ---- User A: accept ----
    user_a_email = f"reuse-a-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": user_a_email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, user_a_email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": token},
    )
    assert resp.status_code == 200, resp.text

    # ---- User B: try reusing same token ----
    user_b_email = f"reuse-b-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": user_b_email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201

    await _login(unauthenticated_memory_client, user_b_email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": token},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "invite_invalid_or_expired"
