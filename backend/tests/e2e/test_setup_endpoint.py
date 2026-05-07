"""E2E: POST /api/v1/system/setup — slug validation, single-tenant only, race handling."""

import secrets

import httpx
import pytest

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _register_pending_owner(client: httpx.AsyncClient, email: str) -> None:
    """Register a user and log in; sets auth + CSRF cookies on client."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    # GET /me to seed the CSRF cookie (returns 401 but sets cookie)
    await client.get("/api/v1/auth/me")
    csrf_name = csrf_cookie_name()
    csrf = client.cookies.get(csrf_name) or ""
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code in (200, 204), f"login failed: {login.status_code} {login.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_name) or csrf


async def test_setup_creates_org_and_owner(fresh_db_unauth_client_single_tenant, session_factory):
    client = fresh_db_unauth_client_single_tenant
    email = f"first-{secrets.token_hex(4)}@example.com"
    await _register_pending_owner(client, email)

    resp = await client.post(
        "/api/v1/system/setup",
        json={"org_name": "Acme Corp", "slug": "acme"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["org_id"]
    assert body["workspace_id"]

    me = await client.get("/api/v1/auth/me")
    assert me.json()["needs_org_setup"] is False

    from sqlalchemy import select

    from cubebox.models import (
        AgentConfig,
        Membership,
        Organization,
        OrganizationMembership,
        OrgRole,
        Role,
        Workspace,
    )

    async with session_factory() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "acme"))
        ).scalar_one()
        ws = (
            await session.execute(select(Workspace).where(Workspace.org_id == org.id))
        ).scalar_one()
        om = (
            await session.execute(
                select(OrganizationMembership).where(OrganizationMembership.org_id == org.id)
            )
        ).scalar_one()
        m = (
            await session.execute(select(Membership).where(Membership.workspace_id == ws.id))
        ).scalar_one()
        ac = (
            await session.execute(select(AgentConfig).where(AgentConfig.workspace_id == ws.id))
        ).scalar_one()
        assert OrgRole(om.role) is OrgRole.OWNER
        assert Role(m.role) is Role.ADMIN
        assert ac is not None


@pytest.mark.parametrize(
    "slug,error_code",
    [
        ("ab", "slug_too_short"),
        ("Acme", "slug_invalid_format"),
        ("-acme", "slug_invalid_format"),
        ("acme-", "slug_invalid_format"),
        ("ac me", "slug_invalid_format"),
        ("acme!", "slug_invalid_format"),
    ],
)
async def test_setup_slug_validation(fresh_db_unauth_client_single_tenant, slug, error_code):
    client = fresh_db_unauth_client_single_tenant
    email = f"first-{secrets.token_hex(4)}@example.com"
    await _register_pending_owner(client, email)

    resp = await client.post(
        "/api/v1/system/setup",
        json={"org_name": "Acme", "slug": slug},
    )
    assert resp.status_code == 422, resp.text
    assert error_code in resp.text


async def test_setup_already_completed_409(
    fresh_db_unauth_client_single_tenant,
):
    client = fresh_db_unauth_client_single_tenant
    email = f"first-{secrets.token_hex(4)}@example.com"
    await _register_pending_owner(client, email)
    r1 = await client.post(
        "/api/v1/system/setup",
        json={"org_name": "Acme", "slug": "acme"},
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/system/setup",
        json={"org_name": "Other", "slug": "other"},
    )
    assert r2.status_code == 409
    assert "setup_already_completed" in r2.text


async def test_setup_disallowed_in_multi_tenant(memory_client):
    """memory_client uses default mode (multi_tenant in production config); confirm 404/409."""
    resp = await memory_client.post(
        "/api/v1/system/setup",
        json={"org_name": "Acme", "slug": "acme"},
    )
    assert resp.status_code in (404, 409)
