"""E2E: SSO enforcement on password login + operator lockout recovery.

These are the security-critical operator paths: once an org's SSO is
``active``, password login for any of its members must return 403
``sso_required`` and steer them to ``/login/{slug}``. Conversely the
``cubeplex admin disable-sso`` CLI must flip the connection back to
``inactive`` so the admin can recover from a broken IdP.

Notes:

- We POST ``/api/v1/admin/sso/.../activate`` through the same admin client
  to mutate connection state — there is no separate write helper for
  ``status`` outside the admin route, which matches what the operator
  actually has access to.
- ``ws_member_client`` lives in the same workspace and org as the default
  admin user, so flipping that org's SSO to ``active`` blocks the member's
  password login. Distinct from ``member_client`` which lives in its own
  isolated org.
- The CLI test exercises a real subprocess (matching ``test_grant_admin_cli``)
  so the integration between Click + AsyncSession + the SSOConnection model
  is verified end-to-end on the worktree DB.
"""

from __future__ import annotations

import os
import subprocess

import httpx
import pytest
from sqlalchemy import select

from cubeplex.models import Organization, SSOConnection
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    WS_MEMBER_TEST_EMAIL,
    WS_MEMBER_TEST_PASSWORD,
)
from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _bypass_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """See tests/e2e/test_sso_admin.py — same rationale."""
    monkeypatch.setattr("cubeplex.sso.oidc._refuse_ssrf_target", lambda url: None)


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("ENV_FOR_DYNACONF", "test")
    return subprocess.run(
        ["uv", "run", "cubeplex", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        check=False,
    )


async def _create_and_activate_sso(client: httpx.AsyncClient) -> str:
    """Create an OIDC SSO connection then activate it. Returns sso_id."""
    resp = await client.post(
        "/api/v1/admin/sso",
        json={
            "protocol": "oidc",
            "display_name": "Corp SSO",
            "config": {
                "client_id": "corp-client",
                "issuer": "https://corp.example.com",
                "authorization_endpoint": "https://corp.example.com/authorize",
                "token_endpoint": "https://corp.example.com/token",
                "jwks_uri": "https://corp.example.com/jwks",
            },
            "client_secret": "corp-secret",
        },
    )
    assert resp.status_code == 201, resp.text
    sso_id: str = resp.json()["id"]
    resp = await client.post(f"/api/v1/admin/sso/{sso_id}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    return sso_id


@pytest.mark.asyncio
async def test_admin_route_round_trip_creates_and_activates_sso(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The admin can drive create → activate via the real HTTP routes.

    Used as a baseline: the rest of the file then writes SSOConnection rows
    directly against the same database with a stable user (the default
    seeded user) to exercise the login enforcement path without depending
    on the admin's randomized credentials.
    """
    admin_c, _ws = admin_client
    sso_id = await _create_and_activate_sso(admin_c)
    assert sso_id


@pytest.mark.asyncio
async def test_password_login_blocked_for_default_user_when_sso_active(
    client: object,  # sync TestClient — triggers DEFAULT user/org seeding
    unauthenticated_memory_client: httpx.AsyncClient,
    db_session: object,
) -> None:
    """Activate SSO on DEFAULT_ORG_ID and verify the seeded DEFAULT user gets 403.

    The ``client`` fixture seeds DEFAULT_TEST_EMAIL as owner of DEFAULT_ORG_ID.
    We bypass the admin route by writing the SSOConnection row directly
    (the route is exercised in test_sso_admin.py); this test focuses on the
    login-side enforcement against a stable, predictable user.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    sess: AsyncSession = db_session  # type: ignore[assignment]

    conn = SSOConnection(
        org_id=DEFAULT_ORG_ID,
        protocol="oidc",
        display_name="Corp SSO",
        status="active",
        provisioning="auto",
        config={
            "client_id": "x",
            "authorization_endpoint": "https://x.example.com/authorize",
            "token_endpoint": "https://x.example.com/token",
        },
        credential_id=None,
    )
    sess.add(conn)
    await sess.commit()

    try:
        # CSRF dance then attempt password login as the DEFAULT user.
        await unauthenticated_memory_client.get("/api/v1/auth/me")
        csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/login",
            data={
                "username": DEFAULT_TEST_EMAIL,
                "password": DEFAULT_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "sso_required"
        # The login_url steers the user to the org-specific SSO entry page.
        assert detail["login_url"].startswith("/login/")
    finally:
        # Clean up so other tests don't see a lingering active SSO.
        await sess.delete(conn)
        await sess.commit()


@pytest.mark.asyncio
async def test_password_login_allowed_in_testing_mode(
    client: object,
    unauthenticated_memory_client: httpx.AsyncClient,
    db_session: object,
) -> None:
    """A `testing`-mode SSO connection must NOT block password login.

    This is what lets an admin verify the IdP wiring before flipping the
    switch that locks out password users.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    sess: AsyncSession = db_session  # type: ignore[assignment]

    conn = SSOConnection(
        org_id=DEFAULT_ORG_ID,
        protocol="oidc",
        display_name="Corp SSO (testing)",
        status="testing",
        provisioning="auto",
        config={
            "client_id": "x",
            "authorization_endpoint": "https://x.example.com/authorize",
            "token_endpoint": "https://x.example.com/token",
        },
        credential_id=None,
    )
    sess.add(conn)
    await sess.commit()

    try:
        await unauthenticated_memory_client.get("/api/v1/auth/me")
        csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/login",
            data={
                "username": DEFAULT_TEST_EMAIL,
                "password": DEFAULT_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code in (200, 204), resp.text
    finally:
        await sess.delete(conn)
        await sess.commit()


@pytest.mark.asyncio
async def test_disable_sso_cli_unblocks_password_login(
    client: object,
    unauthenticated_memory_client: httpx.AsyncClient,
    db_session: object,
    session_factory: object,
) -> None:
    """`cubeplex admin disable-sso --org-slug X` flips status to inactive,
    after which the seeded user can password-login again.

    This is the operator lockout-recovery path. We verify three transitions:

    1. ``active`` SSO blocks login (sanity).
    2. CLI runs successfully and reports the previous status.
    3. After the CLI, login returns 200/204 again.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    sess: AsyncSession = db_session  # type: ignore[assignment]
    maker: async_sessionmaker[AsyncSession] = session_factory  # type: ignore[assignment]

    # Look up the default org's slug (set in conftest seeding).
    org = (
        await sess.execute(
            select(Organization).where(Organization.id == DEFAULT_ORG_ID)  # type: ignore[arg-type]
        )
    ).scalar_one()
    slug = org.slug

    conn = SSOConnection(
        org_id=DEFAULT_ORG_ID,
        protocol="oidc",
        display_name="Corp SSO",
        status="active",
        provisioning="auto",
        config={
            "client_id": "x",
            "authorization_endpoint": "https://x.example.com/authorize",
            "token_endpoint": "https://x.example.com/token",
        },
        credential_id=None,
    )
    sess.add(conn)
    await sess.commit()
    sso_id = conn.id

    try:
        # 1) login blocked.
        await unauthenticated_memory_client.get("/api/v1/auth/me")
        csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/login",
            data={
                "username": DEFAULT_TEST_EMAIL,
                "password": DEFAULT_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "sso_required"

        # 2) Run the CLI in a subprocess against the same worktree DB.
        proc = _run_cli(["admin", "disable-sso", "--org-slug", slug])
        assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        assert "Disabled SSO" in proc.stdout
        assert slug in proc.stdout

        # Confirm DB row flipped (use a fresh session — subprocess committed
        # in its own transaction; the test session needs a re-read).
        async with maker() as fresh:
            updated = (
                await fresh.execute(
                    select(SSOConnection).where(
                        SSOConnection.id == sso_id  # type: ignore[arg-type]
                    )
                )
            ).scalar_one()
            assert updated.status == "inactive"

        # 3) login succeeds again.
        await unauthenticated_memory_client.get("/api/v1/auth/me")
        csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/login",
            data={
                "username": DEFAULT_TEST_EMAIL,
                "password": DEFAULT_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code in (200, 204), resp.text
    finally:
        # The expire_on_commit=False session caches the row; refresh first.
        await sess.refresh(conn)
        await sess.delete(conn)
        await sess.commit()


@pytest.mark.asyncio
async def test_disable_sso_cli_rejects_unknown_org() -> None:
    """CLI fails cleanly when the org slug doesn't exist."""
    proc = _run_cli(["admin", "disable-sso", "--org-slug", "no-such-org-slug"])
    assert proc.returncode != 0
    assert "no org" in (proc.stderr + proc.stdout).lower()


@pytest.mark.asyncio
async def test_list_sso_cli_shows_configured_connection(
    client: object,
    db_session: object,
) -> None:
    """`cubeplex admin list-sso` prints the configured connections table."""
    from sqlalchemy.ext.asyncio import AsyncSession

    sess: AsyncSession = db_session  # type: ignore[assignment]

    org = (
        await sess.execute(
            select(Organization).where(Organization.id == DEFAULT_ORG_ID)  # type: ignore[arg-type]
        )
    ).scalar_one()

    conn = SSOConnection(
        org_id=DEFAULT_ORG_ID,
        protocol="oidc",
        display_name="Listing Demo",
        status="testing",
        provisioning="auto",
        config={
            "client_id": "x",
            "authorization_endpoint": "https://x.example.com/authorize",
            "token_endpoint": "https://x.example.com/token",
        },
        credential_id=None,
    )
    sess.add(conn)
    await sess.commit()

    try:
        proc = _run_cli(["admin", "list-sso"])
        assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        assert org.slug in proc.stdout
        assert "Listing Demo" in proc.stdout
        assert "testing" in proc.stdout
    finally:
        await sess.refresh(conn)
        await sess.delete(conn)
        await sess.commit()


# Sanity check: ws_member_client (a non-admin in the same workspace as the
# default admin) should ALSO be blocked when DEFAULT_ORG_ID's SSO is active.
# This catches the case where the membership join is wrong and the block only
# fires for owners.
@pytest.mark.asyncio
async def test_password_login_blocked_for_ws_member_when_sso_active(
    ws_member_client: object,
    unauthenticated_memory_client: httpx.AsyncClient,
    db_session: object,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    sess: AsyncSession = db_session  # type: ignore[assignment]

    conn = SSOConnection(
        org_id=DEFAULT_ORG_ID,
        protocol="oidc",
        display_name="Corp SSO",
        status="active",
        provisioning="auto",
        config={
            "client_id": "x",
            "authorization_endpoint": "https://x.example.com/authorize",
            "token_endpoint": "https://x.example.com/token",
        },
        credential_id=None,
    )
    sess.add(conn)
    await sess.commit()

    try:
        await unauthenticated_memory_client.get("/api/v1/auth/me")
        csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
        resp = await unauthenticated_memory_client.post(
            "/api/v1/auth/login",
            data={
                "username": WS_MEMBER_TEST_EMAIL,
                "password": WS_MEMBER_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "sso_required"
    finally:
        await sess.refresh(conn)
        await sess.delete(conn)
        await sess.commit()
