"""Unit tests for the enterprise SSO route handlers.

Focus: security-critical guards that don't need a full IdP simulation. The
shared login helpers these handlers call afterwards (forced-SSO enforcement,
cookie issue + redirect) stayed in core and are tested in
tests/unit/test_external_login.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from starlette.requests import Request

pytest.importorskip("cubeplex_ee", reason="enterprise SSO lives in the optional package")

from cubeplex_ee.sso.routes import (  # noqa: E402
    _policy_for,
    sso_initiate,
    sso_oidc_callback,
    sso_saml_acs,
)

from cubeplex.models import Organization, SSOConnection, User  # noqa: E402
from cubeplex.sso.state import SSOStateStore  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("status", "expected_active"),
    [("active", True), ("testing", True), ("inactive", False), ("draft", False)],
)
def test_policy_for_translates_status_to_active(status: str, expected_active: bool) -> None:
    """Only active/testing connections may sign a user in.

    The identity path reads this boolean instead of the connection, so an error
    here silently admits logins through a deactivated IdP.
    """
    conn = SSOConnection(
        org_id="org-1",
        protocol="oidc",
        display_name="T",
        status=status,
        provisioning="auto",
        config={},
    )
    policy = _policy_for(conn)
    assert policy.connection_active is expected_active
    assert policy.org_id == "org-1"


@pytest.mark.parametrize(
    ("provisioning", "expected_auto"),
    [("auto", True), ("invite_only", False)],
)
def test_policy_for_translates_provisioning(provisioning: str, expected_auto: bool) -> None:
    """invite_only is the only value that blocks auto-provisioning."""
    conn = SSOConnection(
        org_id="org-1",
        protocol="oidc",
        display_name="T",
        status="active",
        provisioning=provisioning,
        config={},
    )
    assert _policy_for(conn).auto_provision is expected_auto


def _make_request(redis: fakeredis.aioredis.FakeRedis) -> Request:
    """Build a minimal Starlette Request whose app.state carries the fake redis.

    Route handlers under test only read ``request.app.state.redis``.
    """

    class _State:
        pass

    class _App:
        state = _State()

    app = _App()
    app.state.redis = redis  # type: ignore[attr-defined]
    request = Request({"type": "http", "headers": [], "app": app})
    return request


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


@pytest_asyncio.fixture
async def org_with_oidc_sso(
    sso_session: Any, make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]]
) -> tuple[Organization, SSOConnection]:
    org, _user = await make_org_with_user(email="admin@acme.com")
    conn = SSOConnection(
        org_id=org.id,
        protocol="oidc",
        display_name="Acme OIDC",
        status="active",
        provisioning="auto",
        config={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
            "client_id": "cubeplex-client",
        },
    )
    sso_session.add(conn)
    await sso_session.commit()
    await sso_session.refresh(conn)
    return org, conn


async def test_initiate_oidc_returns_authorize_url_with_state_and_nonce(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
) -> None:
    from cubeplex_ee.sso.routes import SSOInitiateRequest

    org, conn = org_with_oidc_sso
    request = _make_request(fake_redis)
    resp = await sso_initiate(
        SSOInitiateRequest(org_slug=org.slug),
        request,
        sso_session,
    )
    # Authorize URL contains state + nonce + PKCE challenge
    assert "https://idp.example.com/authorize?" in resp.redirect_url
    assert "state=" in resp.redirect_url
    assert "nonce=" in resp.redirect_url
    assert "code_challenge=" in resp.redirect_url
    assert "code_challenge_method=S256" in resp.redirect_url

    # State is consumable by the same store; PKCE verifier was attached.
    state = resp.redirect_url.split("state=")[1].split("&")[0]
    from cubeplex.config import config

    store = SSOStateStore(
        redis=fake_redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    verifier = await store.consume_pkce(state)
    assert verifier is not None
    # No SAML sidecar should be attached for an OIDC flow.
    assert await store.consume_saml_request_id(state) is None
    payload = await store.consume(state)
    assert payload.protocol == "oidc"
    assert payload.sso_connection_id == conn.id
    assert payload.nonce is not None


# --- OIDC callback: protocol guard -----------------------------------------


async def test_oidc_callback_rejects_non_oidc_state(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """A state token forged for the `google` protocol must not pass the
    OIDC callback's protocol guard."""
    from cubeplex.config import config

    store = SSOStateStore(
        redis=fake_redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    bad_state = await store.issue(
        sso_connection_id="sso-fake",
        protocol="google",
        org_id="org-fake",
        oidc_nonce="n",
    )
    request = _make_request(fake_redis)
    resp = await sso_oidc_callback(
        code="any",
        state=bad_state,
        request=request,
        session=sso_session,
        user_manager=None,
    )
    # The callback now redirects to the frontend error page instead of
    # raising — friendlier UX, same security outcome (the user never
    # gets a session cookie).
    assert resp.status_code == 302
    assert "error=sso_invalid_request" in resp.headers["location"]


async def test_oidc_callback_rejects_state_without_nonce(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    from cubeplex.config import config

    store = SSOStateStore(
        redis=fake_redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    bad_state = await store.issue(
        sso_connection_id="sso-fake",
        protocol="oidc",
        org_id="org-fake",
        oidc_nonce=None,  # missing nonce
    )
    request = _make_request(fake_redis)
    resp = await sso_oidc_callback(
        code="any",
        state=bad_state,
        request=request,
        session=sso_session,
        user_manager=None,
    )
    assert resp.status_code == 302
    assert "error=sso_invalid_request" in resp.headers["location"]


# --- SAML ACS: unsolicited rejection ---------------------------------------


async def test_saml_acs_rejects_without_sidecar_request_id(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """A SAML state with no sidecar AuthnRequest ID is an unsolicited /
    IdP-initiated assertion and must be rejected."""
    from cubeplex.config import config

    org, _ = await make_org_with_user(email="x@saml.example")
    conn = SSOConnection(
        org_id=org.id,
        protocol="saml",
        display_name="SAML",
        status="active",
        provisioning="auto",
        config={},
    )
    sso_session.add(conn)
    await sso_session.commit()
    await sso_session.refresh(conn)

    store = SSOStateStore(
        redis=fake_redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    state = await store.issue(sso_connection_id=conn.id, protocol="saml", org_id=org.id)
    # Intentionally do NOT call attach_saml_request_id.

    request = _make_request(fake_redis)
    # Override request.form() to return our payload
    payload_form = {"SAMLResponse": "fake-saml-response", "RelayState": state}

    async def _form() -> dict[str, str]:
        return payload_form

    request._form = payload_form  # type: ignore[attr-defined]
    request.form = _form  # type: ignore[method-assign,assignment]

    resp = await sso_saml_acs(request=request, session=sso_session, user_manager=None)
    assert resp.status_code == 302
    # Same error code as expired-state — the unsolicited path can't be
    # distinguished from a state that survived past its TTL.
    assert "error=sso_state_expired" in resp.headers["location"]


async def test_saml_acs_rejects_non_saml_state(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    from cubeplex.config import config

    store = SSOStateStore(
        redis=fake_redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    bad_state = await store.issue(
        sso_connection_id="sso-fake", protocol="oidc", org_id="org-fake", oidc_nonce="n"
    )
    request = _make_request(fake_redis)
    payload_form = {"SAMLResponse": "x", "RelayState": bad_state}

    async def _form() -> dict[str, str]:
        return payload_form

    request.form = _form  # type: ignore[method-assign,assignment]

    resp = await sso_saml_acs(request=request, session=sso_session, user_manager=None)
    assert resp.status_code == 302
    assert "error=sso_invalid_request" in resp.headers["location"]


# --- forced SSO enforcement ------------------------------------------------
