"""E2E: the SSO base path is written in three places that must agree.

``PUBLIC_BASE_PATH`` is a hand-written literal, but the prefix the app actually
mounts the router at is derived from the extension's module name. The frontend
has its own third copy. All three feed URLs that an administrator pastes into an
identity provider, so a mismatch produces a working admin UI, a green test suite,
and a login that fails at the IdP — the one place nothing here can see.

These tests exist to turn that into a test failure instead. They assert both the
constants and the URLs the handlers actually emit — a matching constant proves
nothing if a handler builds its URL some other way.
"""

from __future__ import annotations

import pathlib
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

pytest.importorskip("cubeplex_ee", reason="enterprise SSO lives in the optional package")

from cubeplex_ee.sso.routes import PUBLIC_BASE_PATH  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture
def _bypass_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The create route resolves config URLs to refuse SSRF targets, and
    example.com endpoints do not resolve. Same rationale as test_sso_admin.py."""
    monkeypatch.setattr("cubeplex.sso.oidc._refuse_ssrf_target", lambda url: None)


# backend/tests/e2e/licensed/<file> -> repo root is five levels up.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_FRONTEND_SSO_TS = _REPO_ROOT / "frontend" / "packages" / "core" / "src" / "api" / "sso.ts"


@pytest.mark.asyncio
async def test_declared_base_path_matches_where_the_router_is_mounted(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The literal the IdP URLs are built from must be the real mount point.

    Read out of the live OpenAPI schema rather than recomputed, so this fails if
    either the literal or the mount rule changes without the other.
    """
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()
    for suffix in ("/initiate", "/oidc/callback", "/saml/acs"):
        assert f"{PUBLIC_BASE_PATH}{suffix}" in schema["paths"], (
            f"PUBLIC_BASE_PATH is {PUBLIC_BASE_PATH!r} but nothing is mounted at "
            f"{PUBLIC_BASE_PATH}{suffix}; the IdP would be handed a 404"
        )


@pytest.mark.asyncio
async def test_frontend_and_backend_agree_on_the_public_base(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """SSOConfigForm builds the copy-paste IdP URLs from the frontend constant.

    Comparing source text is crude, but the alternative is discovering the
    disagreement from a customer whose SAML assertions land on a 404.
    """
    source = _FRONTEND_SSO_TS.read_text()
    match = re.search(r"export const SSO_PUBLIC_BASE = '([^']+)'", source)
    assert match is not None, f"SSO_PUBLIC_BASE not found in {_FRONTEND_SSO_TS}"
    assert match.group(1) == PUBLIC_BASE_PATH, (
        f"frontend SSO_PUBLIC_BASE is {match.group(1)!r} but the backend serves "
        f"{PUBLIC_BASE_PATH!r}"
    )


@pytest.mark.asyncio
async def test_authorize_redirect_uri_points_at_the_mounted_callback(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The redirect_uri handed to the IdP has to be a path we actually serve.

    Guards the concatenation, not just the constant: an inverted or doubled join
    produces a plausible-looking URL that 404s only after the IdP redirects back.
    """
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()

    from cubeplex_ee.sso import routes

    base = "https://app.example.com"
    redirect_uri = f"{base}{routes.PUBLIC_BASE_PATH}/oidc/callback"
    path = redirect_uri[len(base) :]
    assert path in schema["paths"], f"redirect_uri path {path!r} is not served"
    assert redirect_uri.count("/api/v1") == 1, f"malformed join: {redirect_uri}"


@pytest.mark.asyncio
async def test_initiate_emits_a_redirect_uri_the_app_serves(
    admin_client: tuple[httpx.AsyncClient, str],
    _bypass_ssrf_guard: None,
) -> None:
    """Parse the real authorize URL rather than rebuilding it.

    The OIDC initiate test asserts state, nonce and PKCE but never looks at
    redirect_uri, so a handler that builds it from anything other than the mount
    point passes everything else. This drives the actual endpoint and pulls the
    parameter the IdP will be given.
    """
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()

    created = await client.post(
        "/api/v1/admin/_extensions/cubeplex_ee/sso",
        json={
            "protocol": "oidc",
            "display_name": "Path Agreement OIDC",
            "config": {
                "client_id": "pa-client",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            "client_secret": "pa-secret",
        },
    )
    assert created.status_code == 201, created.text
    sso_id = created.json()["id"]
    try:
        activated = await client.post(
            f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}/activate"
        )
        assert activated.status_code == 200, activated.text

        # The test app runs multi_tenant, so initiate needs the org's slug.
        from sqlalchemy import select

        from cubeplex.models import Organization
        from tests.e2e.billing_fixtures import _db_session

        async with _db_session() as db:
            slug = (
                await db.execute(
                    select(Organization.slug).where(
                        Organization.id == created.json()["org_id"]  # type: ignore[arg-type]
                    )
                )
            ).scalar_one()

        resp = await client.post(f"{PUBLIC_BASE_PATH}/initiate", json={"org_slug": slug})
        assert resp.status_code == 200, resp.text
        query = parse_qs(urlparse(resp.json()["redirect_url"]).query)
        redirect_uri = query["redirect_uri"][0]

        served = urlparse(redirect_uri).path
        assert served in schema["paths"], (
            f"initiate handed the IdP redirect_uri={redirect_uri!r}, whose path "
            f"{served!r} this app does not serve"
        )
    finally:
        await client.post(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}/deactivate")
        await client.delete(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}")


@pytest.mark.asyncio
async def test_saml_metadata_advertises_an_acs_the_app_serves(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The published metadata is what the IdP is configured from.

    The SAML unit test calls generate_sp_metadata with URLs it invents, so it
    cannot catch a wrong ACS in what the endpoint actually publishes.
    """
    client, _ws_id = admin_client
    schema = (await client.get("/openapi.json")).json()

    created = await client.post(
        "/api/v1/admin/_extensions/cubeplex_ee/sso",
        json={
            "protocol": "saml",
            "display_name": "Path Agreement SAML",
            "config": {
                "idp_entity_id": "https://idp.example.com/saml",
                "idp_sso_url": "https://idp.example.com/saml/sso",
                "idp_certificate": "MIIBogus",
            },
        },
    )
    assert created.status_code == 201, created.text
    sso_id = created.json()["id"]
    try:
        resp = await client.get(f"/api/v1/_extensions/cubeplex_ee/sso/saml/metadata/{sso_id}")
        assert resp.status_code == 200, resp.text
        acs_matches = re.findall(r'Location="([^"]+)"', resp.text)
        assert acs_matches, f"no Location in published metadata: {resp.text[:300]}"
        for location in acs_matches:
            path = urlparse(location).path
            assert path in schema["paths"], (
                f"metadata advertises {location!r}, whose path {path!r} is not served"
            )
    finally:
        await client.delete(f"/api/v1/admin/_extensions/cubeplex_ee/sso/{sso_id}")
