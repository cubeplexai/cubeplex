"""E2E: POST /workspaces honors deployment.mode for org_id resolution."""

import secrets

import pytest

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client, email: str) -> None:
    """Login and seed CSRF header (mirrors conftest._login_and_attach)."""
    await client.get("/api/v1/auth/me")  # obtain CSRF cookie (401 but sets cookie)
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code in (200, 204), f"login failed: {resp.status_code} {resp.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


async def test_single_tenant_forces_singleton_org(
    fresh_db_unauth_client_single_tenant, session_factory
):
    client = fresh_db_unauth_client_single_tenant
    email = f"u-{secrets.token_hex(4)}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    await _login(client, email)
    # Onboarding bootstraps the singleton org + first workspace.
    onboarding = await client.post(
        "/api/v1/onboarding",
        json={"org_name": "Acme", "org_slug": "acme", "workspace_name": "Personal"},
    )
    assert onboarding.status_code == 201, (
        f"onboarding failed: {onboarding.status_code} {onboarding.text}"
    )
    # Resolve the singleton org id from /me (onboarding returns workspace_id only).
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    real_org_id = me.json()["org_memberships"][0]["org_id"]

    # Submit a fake org_id; backend should ignore and use singleton.
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "P2", "org_id": "org_fake_999"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] == real_org_id


async def test_multi_tenant_validates_membership(member_client_org_a, session_factory):
    """User from org_a posts under non-member org_id → 403."""
    from cubeplex.repositories import OrganizationRepository

    client_a, _ = member_client_org_a

    # Create org_b so it exists in the database.
    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        org_b = await org_repo.create(name="Org B", slug="org-b")
        org_b_id = org_b.id
        await session.commit()

    # User is a member of their own org (org_a), but not org_b.
    # Attempt to create workspace in org_b should fail with 403.
    resp = await client_a.post(
        "/api/v1/workspaces",
        json={"name": "X", "org_id": org_b_id},
    )
    assert resp.status_code == 403, resp.text
