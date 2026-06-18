"""Unit tests for SSO auth routes.

Focus: security-critical guards that don't need a full IdP simulation.
End-to-end OIDC/SAML flows are covered by Task 15 E2E.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from cubebox.api.routes.v1.sso import (
    _enforce_forced_sso_for_user,
    _login_and_redirect,
    sso_initiate,
    sso_oidc_callback,
    sso_saml_acs,
)
from cubebox.models import (
    Membership,
    Organization,
    Role,
    SSOConnection,
    User,
    Workspace,
)
from cubebox.sso.state import SSOStateStore

pytestmark = pytest.mark.asyncio


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
            "client_id": "cubebox-client",
        },
    )
    sso_session.add(conn)
    await sso_session.commit()
    await sso_session.refresh(conn)
    return org, conn


# --- org-info ---------------------------------------------------------------


async def test_org_info_returns_sso_enabled(
    sso_session: Any,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
) -> None:
    from cubebox.api.routes.v1.sso import get_org_info

    org, _ = org_with_oidc_sso
    resp = await get_org_info(org.slug, sso_session)
    assert resp.org_name == org.name
    assert resp.sso_enabled is True
    assert resp.sso_protocol == "oidc"


async def test_org_info_no_sso(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    from cubebox.api.routes.v1.sso import get_org_info

    org, _ = await make_org_with_user(email="solo@example.com")
    resp = await get_org_info(org.slug, sso_session)
    assert resp.sso_enabled is False
    assert resp.sso_protocol is None


async def test_org_info_404_for_unknown_slug(sso_session: Any) -> None:
    from cubebox.api.routes.v1.sso import get_org_info

    with pytest.raises(HTTPException) as exc_info:
        await get_org_info("does-not-exist", sso_session)
    assert exc_info.value.status_code == 404


# --- initiate ---------------------------------------------------------------


async def test_initiate_oidc_returns_authorize_url_with_state_and_nonce(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
) -> None:
    from cubebox.api.routes.v1.sso import SSOInitiateRequest

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
    from cubebox.config import config

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
    from cubebox.config import config

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
    from cubebox.config import config

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
    from cubebox.config import config

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
    from cubebox.config import config

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


async def test_enforce_forced_sso_blocks_when_allowed_org_is_none(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """A user in an org with active SSO must not log in via a path that
    doesn't go through that org (e.g. social login)."""
    org, user = await make_org_with_user(email="member@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _enforce_forced_sso_for_user(sso_session, user, allowed_org_id=None)
    assert exc_info.value.status_code == 403
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "sso_required"


async def test_enforce_forced_sso_blocks_when_allowed_org_is_different(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, user = await make_org_with_user(email="m@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _enforce_forced_sso_for_user(sso_session, user, allowed_org_id="org-some-other")
    assert exc_info.value.status_code == 403


async def test_enforce_forced_sso_passes_when_allowed_org_matches(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, user = await make_org_with_user(email="m2@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    # Should NOT raise
    await _enforce_forced_sso_for_user(sso_session, user, allowed_org_id=org.id)


async def test_enforce_forced_sso_ignores_testing_status(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """`testing` status connections must not trigger the forced-SSO block —
    only `active` does."""
    org, user = await make_org_with_user(email="t@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="testing",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    await _enforce_forced_sso_for_user(sso_session, user, allowed_org_id=None)


# --- _login_and_redirect: workspace pick by membership ---------------------


async def test_login_and_redirect_picks_workspace_user_belongs_to(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """The SSO redirect must land on a workspace where the user has a
    Membership — not just any workspace in the org."""
    org, user = await make_org_with_user(email="ws-user@example.com")

    # Workspace the user is NOT a member of — must not be picked.
    other_ws = Workspace(org_id=org.id, name="Other WS")
    sso_session.add(other_ws)
    await sso_session.flush()

    # Workspace the user IS a member of.
    my_ws = Workspace(org_id=org.id, name="My WS")
    sso_session.add(my_ws)
    await sso_session.flush()
    sso_session.add(Membership(user_id=user.id, workspace_id=my_ws.id, role=Role.MEMBER))
    await sso_session.commit()

    request = _make_request(fake_redis)
    resp = await _login_and_redirect(request, sso_session, user)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert f"/w/{my_ws.id}" in location
    assert other_ws.id not in location


async def test_login_and_redirect_falls_back_to_base_when_no_membership(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    _, user = await make_org_with_user(email="no-ws@example.com")
    request = _make_request(fake_redis)
    resp = await _login_and_redirect(request, sso_session, user)
    assert resp.status_code == 302
    # No workspace was a membership target → redirect to base URL.
    assert "/w/" not in resp.headers["location"]
