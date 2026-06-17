"""E2E: admin SSO CRUD + activation lifecycle via HTTP.

Covers the operator-facing surface end-to-end: create → get → update →
activate → deactivate → delete, plus duplicate rejection, status-transition
guards, the deactivate-before-delete rule, and the OIDC discovery passthrough
on a real running app. Heavy protocol-level coverage (token validation,
SAML signature verification, nonce/PKCE, identity resolution) is in the
unit suite — this file just verifies the routes wire together against a
real Postgres + encryption backend + ``require_org_admin`` gate.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _bypass_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E tests use *.example.com hosts that don't resolve in CI sandboxes;
    bypass the SSRF guard so the admin-route fail-closed doesn't masquerade
    as a DNS failure. The guard itself is exercised in
    ``tests/unit/test_admin_sso_routes.py::test_discover_oidc_refuses_*``."""
    monkeypatch.setattr("cubebox.sso.oidc._refuse_ssrf_target", lambda url: None)


@pytest.mark.asyncio
async def test_admin_sso_crud_lifecycle(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Full lifecycle: create OIDC → get → update → activate → deactivate → delete."""
    client, _ws = admin_client

    # Initially no SSO connection.
    resp = await client.get("/api/v1/admin/sso")
    assert resp.status_code == 200, resp.text
    assert resp.json() is None

    # Create with an OIDC config + client secret (exercises vault store).
    resp = await client.post(
        "/api/v1/admin/sso",
        json={
            "protocol": "oidc",
            "display_name": "Test OIDC SSO",
            "provisioning": "auto",
            "config": {
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
                "userinfo_endpoint": "https://idp.example.com/userinfo",
                "client_id": "test-client-id",
                "scopes": ["openid", "email", "profile"],
            },
            "client_secret": "test-client-secret",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    sso_id = created["id"]
    assert created["protocol"] == "oidc"
    assert created["status"] == "testing"
    assert created["provisioning"] == "auto"
    assert created["display_name"] == "Test OIDC SSO"

    # Get returns the same row.
    resp = await client.get("/api/v1/admin/sso")
    assert resp.status_code == 200
    assert resp.json()["id"] == sso_id

    # Update display name + provisioning.
    resp = await client.put(
        f"/api/v1/admin/sso/{sso_id}",
        json={"display_name": "Renamed SSO", "provisioning": "invite_only"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Renamed SSO"
    assert body["provisioning"] == "invite_only"

    # Activate.
    resp = await client.post(f"/api/v1/admin/sso/{sso_id}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    # Re-activate from `active` is a no-op rejection (only from testing/inactive).
    resp = await client.post(f"/api/v1/admin/sso/{sso_id}/activate")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_status_transition"

    # Delete while active is refused.
    resp = await client.delete(f"/api/v1/admin/sso/{sso_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "deactivate_before_delete"

    # Deactivate, then delete succeeds.
    resp = await client.post(f"/api/v1/admin/sso/{sso_id}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"

    resp = await client.delete(f"/api/v1/admin/sso/{sso_id}")
    assert resp.status_code == 204

    # Gone.
    resp = await client.get("/api/v1/admin/sso")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_admin_sso_duplicate_rejected(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Creating a second SSO connection in the same org returns 409."""
    client, _ws = admin_client
    payload = {
        "protocol": "oidc",
        "display_name": "First",
        "config": {
            "client_id": "a",
            "issuer": "https://a.example.com",
            "authorization_endpoint": "https://a.example.com/authorize",
            "token_endpoint": "https://a.example.com/token",
            "jwks_uri": "https://a.example.com/jwks",
        },
    }
    resp = await client.post("/api/v1/admin/sso", json=payload)
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/api/v1/admin/sso",
        json={**payload, "display_name": "Second"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "sso_already_configured"


@pytest.mark.asyncio
async def test_admin_sso_deactivate_requires_active(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Deactivate is only valid from `active` — testing → deactivate is 409."""
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sso",
        json={
            "protocol": "oidc",
            "display_name": "Test SSO",
            "config": {
                "client_id": "c",
                "issuer": "https://c.example.com",
                "authorization_endpoint": "https://c.example.com/authorize",
                "token_endpoint": "https://c.example.com/token",
                "jwks_uri": "https://c.example.com/jwks",
            },
        },
    )
    assert resp.status_code == 201
    sso_id = resp.json()["id"]
    # status is `testing` here; deactivate must reject.
    resp = await client.post(f"/api/v1/admin/sso/{sso_id}/deactivate")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_admin_sso_requires_admin(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Non-admin members cannot read or write the admin SSO endpoint."""
    client, _ws = member_client
    resp = await client.get("/api/v1/admin/sso")
    assert resp.status_code in (401, 403), resp.text
    resp = await client.post(
        "/api/v1/admin/sso",
        json={
            "protocol": "oidc",
            "display_name": "Forbidden",
            "config": {},
        },
    )
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_admin_sso_unknown_id_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws = admin_client
    resp = await client.post("/api/v1/admin/sso/sso_does_not_exist/activate")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "sso_not_found"
