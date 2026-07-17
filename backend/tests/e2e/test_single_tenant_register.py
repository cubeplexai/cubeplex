"""E2E: single_tenant register branches on org_count."""

import secrets

import httpx
import pytest
from sqlalchemy import select

from cubeplex.models import Membership, Organization, OrganizationMembership, User
from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    """Log in on a fresh client; sets auth + CSRF cookies."""
    # GET /me to seed the CSRF cookie (returns 401 but sets cookie)
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


async def test_first_register_pending_owner(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient, session_factory
):
    """Fresh DB, single_tenant: first register creates ONLY the User row."""
    email = f"first-{secrets.token_hex(4)}@example.com"
    resp = await fresh_db_unauth_client_single_tenant.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text

    await _login(fresh_db_unauth_client_single_tenant, email, "password123")

    me = await fresh_db_unauth_client_single_tenant.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is True

    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        assert (await session.execute(select(Organization))).first() is None
        assert (
            await session.execute(
                select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
            )
        ).first() is None
        assert (
            await session.execute(select(Membership).where(Membership.user_id == user.id))
        ).first() is None


async def test_second_register_during_setup_returns_409(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
):
    e1 = f"first-{secrets.token_hex(4)}@example.com"
    await fresh_db_unauth_client_single_tenant.post(
        "/api/v1/auth/register",
        json={"email": e1, "password": "password123"},
    )

    e2 = f"second-{secrets.token_hex(4)}@example.com"
    resp = await fresh_db_unauth_client_single_tenant.post(
        "/api/v1/auth/register",
        json={"email": e2, "password": "password123"},
    )
    assert resp.status_code == 409, resp.text
    assert "setup_in_progress" in resp.text
