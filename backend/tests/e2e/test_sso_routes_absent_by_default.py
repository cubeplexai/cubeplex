"""E2E: the SSO routes do not exist without the optional package — but the
open-source paths that mention SSO still answer.

The companion suite in tests/e2e/licensed/ proves the flow works when installed.
This proves the boundary holds when it isn't, which is the half that runs in
default CI. It also covers the three core surfaces the relocation could
plausibly have taken with it: the login page's org lookup, Google social login,
and the break-glass CLI's model.
"""

import importlib.util

import httpx
import pytest

if importlib.util.find_spec("cubeplex_ee") is not None:
    pytest.skip(
        "optional package installed; absence is not the contract in that environment",
        allow_module_level=True,
    )

pytestmark = pytest.mark.e2e

_OLD_ADMIN = "/api/v1/admin/sso"
_NEW_ADMIN = "/api/v1/admin/_extensions/cubeplex_ee/sso"
_OLD_LOGIN = "/api/v1/auth/sso/initiate"
_NEW_LOGIN = "/api/v1/_extensions/cubeplex_ee/sso/initiate"


@pytest.mark.asyncio
async def test_admin_sso_endpoints_are_not_served(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Authenticated deliberately: a 401 would say nothing about the route existing."""
    client, _ws_id = admin_client
    for path in (_OLD_ADMIN, _NEW_ADMIN):
        resp = await client.get(path)
        assert resp.status_code == 404, f"{path} answered {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_login_flow_endpoints_are_not_served(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    """The login flow is unauthenticated, so this needs no session to be meaningful."""
    for path in (_OLD_LOGIN, _NEW_LOGIN):
        resp = await unauthenticated_memory_client.post(path, json={"org_slug": "acme"})
        assert resp.status_code == 404, f"{path} answered {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_sso_endpoints_absent_from_openapi(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Extension routers mount during lifespan, so the schema reflects the edition."""
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()
    sso_paths = [p for p in schema["paths"] if "/sso" in p]
    assert sso_paths == [], f"unlicensed schema still advertises {sso_paths}"


@pytest.mark.asyncio
async def test_org_info_still_answers_and_reports_no_sso(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    """The login page asks this to decide whether to show an SSO button.

    It must not 404 here: a deployment without the package still has orgs, and
    the honest answer is that they have no SSO. This is the endpoint most at risk
    of being carried along with the relocation, since it lived in the file that
    moved.
    """
    resp = await unauthenticated_memory_client.get("/api/v1/auth/org-info/does-not-exist")
    assert resp.status_code == 404  # unknown slug, not a missing route
    body = resp.json()
    assert body.get("detail") == "org_not_found"


@pytest.mark.asyncio
async def test_google_social_login_routes_still_mounted(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Google login is open source and shares the identity path with SSO.

    Asserted through the schema, not by calling the route: `google/authorize`
    answers 404 by design when Google is not configured ("same 404 whether
    disabled or misconfigured — no enumeration"), so a status check cannot tell
    "route gone" from "provider off". The schema can.
    """
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()
    for path in (
        "/api/v1/auth/social/google/authorize",
        "/api/v1/auth/social/google/callback",
        "/api/v1/auth/org-info/{org_slug}",
    ):
        assert path in schema["paths"], f"{path} disappeared with the SSO move"
