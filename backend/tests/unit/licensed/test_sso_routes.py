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
from fastapi import HTTPException  # noqa: E402

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


# --- the enterprise callback driven to completion ---------------------------
#
# The handler tests above stop at malformed state, so none of them reach the
# handoff this relocation created: EE resolves the identity, then calls back into
# the core module for forced-SSO enforcement and cookie issue. Only exchange_code
# — the outer IdP call — is stubbed here.
#
# Both cases sign in a user who is *already* a member of the connection's org.
# That is not just convenience: `uq_org_membership_owner` is declared with
# `postgresql_where`, which SQLite ignores, so under the unit-test engine it
# degrades into a plain unique index on org_id and no org can hold a second
# member. Auto-provisioning a new user into an existing org is therefore only
# testable against Postgres.


async def _drive_oidc_callback(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: Any,
    user_manager: Any,
    redis: fakeredis.aioredis.FakeRedis,
    conn: SSOConnection,
    email: str,
) -> Any:
    from cubeplex_ee.sso import routes

    from cubeplex.sso.oidc import OIDCUserInfo

    async def fake_exchange(cfg: Any, **kwargs: Any) -> OIDCUserInfo:
        return OIDCUserInfo(
            sub="ent-sub-1",
            email=email,
            email_verified=True,
            name="Enterprise User",
            claims={"sub": "ent-sub-1", "email": email, "name": "Enterprise User"},
        )

    monkeypatch.setattr(routes, "exchange_code", fake_exchange)

    async def fake_secret(request: Any, session: Any, conn: Any) -> str:
        return "client-secret"

    monkeypatch.setattr(routes, "_get_client_secret", fake_secret)

    # Same secret the handler's own store uses, or consume() rejects the state.
    from cubeplex.config import config

    store = SSOStateStore(
        redis=redis, secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode()
    )
    state = await store.issue(
        sso_connection_id=conn.id, protocol="oidc", org_id=conn.org_id, oidc_nonce="n"
    )
    await store.attach_pkce(state=state, verifier="verifier-123")

    return await routes.sso_oidc_callback(
        code="auth-code",
        state=state,
        request=_make_request(redis),
        session=session,
        user_manager=user_manager,
    )


async def test_enterprise_oidc_callback_completes_and_issues_a_session(
    sso_session: Any,
    sso_user_manager: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exchange -> mapping -> resolve_identity -> forced-SSO -> cookie + redirect.

    resolve_identity, enforce_forced_sso_for_user and login_and_redirect all live
    in the open-source package; this is the only test that proves the licensed
    callback still reaches them after the split.
    """
    _org, conn = org_with_oidc_sso
    resp = await _drive_oidc_callback(
        monkeypatch,
        session=sso_session,
        user_manager=sso_user_manager,
        redis=fake_redis,
        conn=conn,
        email="admin@acme.com",  # the org_with_oidc_sso fixture's existing member
    )
    assert resp.status_code == 302
    assert resp.headers.getlist("set-cookie"), "a completed SSO login must set the session cookie"


async def test_enterprise_oidc_callback_passes_its_own_org_to_forced_sso(
    sso_session: Any,
    sso_user_manager: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allowed_org_id must be the connection's org, not None.

    Passing None — the value the Google path uses — would make every enterprise
    login through a forced-SSO org fail, because the org that forces SSO is the
    very one the user is authenticating against.
    """
    _org, conn = org_with_oidc_sso
    assert conn.status == "active", "fixture must be a forced-SSO connection"

    resp = await _drive_oidc_callback(
        monkeypatch,
        session=sso_session,
        user_manager=sso_user_manager,
        redis=fake_redis,
        conn=conn,
        email="admin@acme.com",
    )
    # A 302 to the workspace, not the SSO error page.
    assert resp.status_code == 302
    assert "/sso/callback?error=" not in resp.headers.get("location", "")


async def test_enterprise_callback_refuses_a_forced_sso_user_from_another_org(
    sso_session: Any,
    sso_user_manager: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member of forced-SSO org A must not sign in through org B's *testing* IdP.

    This is the case that makes deleting the enforce_forced_sso_for_user call
    detectable. The sibling tests sign in a member of the connection's own org,
    where the guard is meant to let them through, so removing it leaves them green.

    Org B's connection is `testing` on purpose. `forced_orgs` counts only `active`
    connections, so provisioning the victim into B does not make B satisfy their
    forced-SSO obligation — and an admin's half-configured test connection must
    not become a way around org A's requirement. With B `active` instead, the
    guard correctly permits the login, which is why that variant proves nothing.
    """
    org_a, victim = await make_org_with_user(email="victim@corp-a.com")
    sso_session.add(
        SSOConnection(
            org_id=org_a.id,
            protocol="oidc",
            display_name="Corp A SSO",
            status="active",
            provisioning="auto",
            config={},
        )
    )

    # Org B is created without a member: SQLite collapses the partial
    # owner-uniqueness index into a plain one (see the note above), so an org that
    # already has a member cannot take the provisioned victim.
    org_b = Organization(name="Corp B", slug="corp-b")
    sso_session.add(org_b)
    await sso_session.flush()
    conn_b = SSOConnection(
        org_id=org_b.id,
        protocol="oidc",
        display_name="Corp B SSO",
        status="testing",
        provisioning="auto",
        config={
            "issuer": "https://idp-b.example.com",
            "authorization_endpoint": "https://idp-b.example.com/authorize",
            "token_endpoint": "https://idp-b.example.com/token",
            "jwks_uri": "https://idp-b.example.com/jwks",
            "client_id": "corp-b-client",
        },
    )
    sso_session.add(conn_b)
    await sso_session.commit()
    await sso_session.refresh(conn_b)

    # enforce_forced_sso_for_user raises HTTPException(403); the callback does not
    # catch it, so it propagates rather than becoming an error redirect.
    with pytest.raises(HTTPException) as exc_info:
        await _drive_oidc_callback(
            monkeypatch,
            session=sso_session,
            user_manager=sso_user_manager,
            redis=fake_redis,
            conn=conn_b,
            email=victim.email,
        )
    assert exc_info.value.status_code == 403
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "sso_required"
