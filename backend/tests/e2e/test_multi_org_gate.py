"""E2E: single_tenant caps organizations at 1 unless the license has multi_org."""

import secrets

import httpx
import pytest
from fastapi_users.schemas import BaseUserCreate
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.auth.users import UserManager
from cubeplex.db.engine import _build_database_url
from cubeplex.models import User
from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


async def _make_unaffiliated_user() -> tuple[str, str]:
    """Create a user with no org and no workspace membership.

    Registration cannot produce this state once an org exists — the single_tenant
    hook attaches later registrants to the singleton org, and its user_count guard
    rejects a second pending owner while no org exists. The state is still
    reachable in a live deployment (an admin removing someone from the only org),
    and it is the one that walks into onboarding's full mode with an org already
    present, so it is what the gate has to hold against.
    """
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            email = f"unaffiliated-{secrets.token_hex(4)}@example.com"
            password = "StrongPass1!"
            manager = UserManager(SQLAlchemyUserDatabase(session, User))
            await manager.create(BaseUserCreate(email=email, password=password), safe=False)
            await session.commit()
            return email, password
    finally:
        await engine.dispose()


def _onboard_body(tag: str) -> dict[str, str]:
    return {
        "org_name": f"Org {tag}",
        "org_slug": f"org-{tag}",
        "workspace_name": f"WS {tag}",
    }


async def _create_first_org(client: httpx.AsyncClient) -> None:
    """Register the first owner and complete full onboarding — org #1."""
    email = f"owner-{secrets.token_hex(4)}@example.com"
    password = "StrongPass1!"
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    await _login(client, email, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_second_org_blocked_without_license(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = fresh_db_unauth_client_single_tenant
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False)

    await _create_first_org(client)

    email, password = await _make_unaffiliated_user()
    client.cookies.clear()
    await _login(client, email, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "multi_org_requires_license"


@pytest.mark.asyncio
async def test_second_org_allowed_with_multi_org_license(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = fresh_db_unauth_client_single_tenant
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False)
    # Key parsing is unit-tested; this covers the gate wiring only.
    monkeypatch.setattr("cubeplex.plugins.license.has_feature", lambda name: name == "multi_org")

    await _create_first_org(client)

    email, password = await _make_unaffiliated_user()
    client.cookies.clear()
    await _login(client, email, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_first_org_is_never_gated(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OSS build must still be able to bootstrap its one organization."""
    client = fresh_db_unauth_client_single_tenant
    monkeypatch.setattr("cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False)
    await _create_first_org(client)
