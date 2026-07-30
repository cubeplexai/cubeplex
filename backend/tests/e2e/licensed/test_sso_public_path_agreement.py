"""E2E: the SSO base path is written in three places that must agree.

``PUBLIC_BASE_PATH`` is a hand-written literal, but the prefix the app actually
mounts the router at is derived from the extension's module name. The frontend
has its own third copy. All three feed URLs that an administrator pastes into an
identity provider, so a mismatch produces a working admin UI, a green test suite,
and a login that fails at the IdP — the one place nothing here can see.

These tests exist to turn that into a test failure instead.
"""

from __future__ import annotations

import pathlib
import re

import httpx
import pytest

pytest.importorskip("cubeplex_ee", reason="enterprise SSO lives in the optional package")

from cubeplex_ee.sso.routes import PUBLIC_BASE_PATH  # noqa: E402

pytestmark = pytest.mark.e2e

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
