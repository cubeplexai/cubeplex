"""E2E: driving SSO activation through the real admin routes.

Split out of tests/e2e/test_sso_enforcement.py, which keeps the rest. That file
guards an open-source invariant — password login is refused when the user's org
has active SSO — and writes connection rows directly on purpose, so it runs on a
default install. Only this one case needs the admin CRUD surface, which the
optional package owns.
"""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("cubeplex_ee", reason="admin SSO CRUD lives in the optional package")

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _bypass_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The create route resolves every config URL to refuse SSRF targets, and
    example.com endpoints do not resolve. Same rationale as
    tests/e2e/licensed/test_sso_admin.py."""
    monkeypatch.setattr("cubeplex.sso.oidc._refuse_ssrf_target", lambda url: None)


async def _create_and_activate_sso(client: httpx.AsyncClient) -> str:
    """Create an OIDC SSO connection then activate it. Returns sso_id."""
    resp = await client.post(
        "/api/v1/admin/_extensions/cubeplex_ee/sso",
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
    resp = await client.post(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    return sso_id


@pytest.mark.asyncio
async def test_admin_route_round_trip_creates_and_activates_sso(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The admin can drive create → activate via the real HTTP routes.

    This was the baseline case in tests/e2e/test_sso_enforcement.py, which now
    writes its rows directly so it can run without the optional package.
    """
    admin_c, _ws = admin_client
    sso_id = await _create_and_activate_sso(admin_c)
    assert sso_id

    # Leave no active row behind. An active connection in the default org makes
    # every later password login in this database 403 with sso_required, and on a
    # subsequent run without the package installed it also trips the
    # unserviceable-SSO startup report. Deactivate first: delete refuses while
    # active, which is the route's own rule.
    resp = await admin_c.post(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}/deactivate")
    assert resp.status_code == 200, resp.text
    resp = await admin_c.delete(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}")
    assert resp.status_code in (200, 204), resp.text
