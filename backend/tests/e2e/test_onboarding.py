"""E2E: post-registration onboarding wizard."""

import secrets

import httpx
import pytest
from sqlalchemy import select

from cubebox.models import Organization, OrganizationMembership, User
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
async def test_onboarding_full_multi_tenant(
    unauthenticated_memory_client: httpx.AsyncClient,
    session_factory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full onboarding (multi_tenant): register -> login -> onboard -> me has workspace."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    email = f"onboard-full-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"
    org_slug = f"full-{secrets.token_hex(4)}"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text

    await _login(unauthenticated_memory_client, email, password)

    # GET /me shows needs_onboarding
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is True

    # Onboarding creates org + workspace
    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={
            "org_name": f"Full Org {email}",
            "org_slug": org_slug,
            "workspace_name": "My Workspace",
        },
    )
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["workspace_id"]
    assert ws_id

    # GET /me shows needs_onboarding false + org_memberships
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    me_data = me.json()
    assert me_data["needs_onboarding"] is False
    assert len(me_data["org_memberships"]) > 0

    # Cleanup
    maker: async_sessionmaker[AsyncSession] = session_factory
    async with maker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            await session.execute(select(Organization).where(Organization.slug == org_slug))


@pytest.mark.asyncio
async def test_onboarding_full_single_tenant(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    session_factory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full onboarding (single_tenant first owner): register -> login -> onboard."""

    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    email = f"onboard-st-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"
    org_slug = f"st-{secrets.token_hex(4)}"

    resp = await fresh_db_unauth_client_single_tenant.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text

    await _login(fresh_db_unauth_client_single_tenant, email, password)

    me = await fresh_db_unauth_client_single_tenant.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is True

    resp = await fresh_db_unauth_client_single_tenant.post(
        "/api/v1/onboarding",
        json={
            "org_name": f"ST Org {email}",
            "org_slug": org_slug,
            "workspace_name": "My WS",
        },
    )
    assert resp.status_code == 201, resp.text

    me = await fresh_db_unauth_client_single_tenant.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is False
    assert len(me.json()["org_memberships"]) > 0


@pytest.mark.asyncio
async def test_onboarding_workspace_only(
    unauthenticated_memory_client: httpx.AsyncClient,
    session_factory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-only: user with org membership but no workspace -> onboarding creates workspace."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from cubebox.auth.users import _slugify_org_name
    from cubebox.models import OrgRole
    from cubebox.repositories import (
        OrganizationMembershipRepository,
        OrganizationRepository,
    )

    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    email = f"onboard-wsonly-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text

    # Pre-grant an org membership via DB
    maker: async_sessionmaker[AsyncSession] = session_factory
    async with maker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        org = await OrganizationRepository(session).create(
            name=f"PreOrg {email}", slug=_slugify_org_name(f"PreOrg {email}")
        )
        await OrganizationMembershipRepository(session).grant(
            user_id=user.id, org_id=org.id, role=OrgRole.MEMBER
        )
        await session.commit()

    await _login(unauthenticated_memory_client, email, password)

    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is True

    # Workspace-only: no org fields needed
    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"workspace_name": "My WS"},
    )
    assert resp.status_code == 201, resp.text

    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["needs_onboarding"] is False


@pytest.mark.asyncio
async def test_onboarding_slug_collision(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slug collision returns 409 slug_taken."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    slug = f"shared-{secrets.token_hex(4)}"

    # First user registers and onboard with slug
    email1 = f"slug1-{secrets.token_hex(4)}@example.com"
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email1, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201
    await _login(unauthenticated_memory_client, email1, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"org_name": "Org1", "org_slug": slug, "workspace_name": "WS1"},
    )
    assert resp.status_code == 201, resp.text

    # Second user tries same slug
    email2 = f"slug2-{secrets.token_hex(4)}@example.com"
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email2, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201
    await _login(unauthenticated_memory_client, email2, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"org_name": "Org2", "org_slug": slug, "workspace_name": "WS2"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "slug_taken"


@pytest.mark.asyncio
async def test_onboarding_already_onboarded(
    unauthenticated_memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-onboarded user returns 409 onboarding_not_required."""
    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)
    email = f"already-{secrets.token_hex(4)}@example.com"
    slug = f"already-{secrets.token_hex(4)}"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201
    await _login(unauthenticated_memory_client, email, "StrongPass1!")

    # First onboarding succeeds
    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"org_name": "Org", "org_slug": slug, "workspace_name": "WS"},
    )
    assert resp.status_code == 201

    # Second onboarding fails
    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={"org_name": "Org2", "org_slug": f"{slug}-2", "workspace_name": "WS2"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "onboarding_not_required"


@pytest.mark.asyncio
async def test_onboarding_rollback_on_failure(
    unauthenticated_memory_client: httpx.AsyncClient,
    session_factory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When bootstrap raises, onboarding returns 500 and no org row is created."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr("cubebox.auth.email_otp.is_email_verification_enabled", lambda: False)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr("cubebox.auth.users._bootstrap_org_and_workspace", _fail)

    email = f"rollback-{secrets.token_hex(4)}@example.com"

    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1!"},
    )
    assert resp.status_code == 201
    await _login(unauthenticated_memory_client, email, "StrongPass1!")

    resp = await unauthenticated_memory_client.post(
        "/api/v1/onboarding",
        json={
            "org_name": "FailOrg",
            "org_slug": f"fail-{secrets.token_hex(4)}",
            "workspace_name": "FailWS",
        },
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "ONBOARDING_FAILED"

    # Verify no org was created for this user
    maker: async_sessionmaker[AsyncSession] = session_factory
    async with maker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            org_rows = (
                await session.execute(
                    select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
                )
            ).all()
            assert len(org_rows) == 0, "No org membership should exist after failed onboarding"
