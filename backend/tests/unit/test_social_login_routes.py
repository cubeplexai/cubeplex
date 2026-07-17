"""Unit tests for Google social login routes.

Focus: state/nonce/PKCE/protocol-assertion guards and the forced-SSO
crossover from social login. The full Google token-exchange flow (mocked
token endpoint + JWKS + ID token signing) is deferred to Task 15 E2E —
keep the unit cover surface-level here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from cubeplex.api.routes.v1.social_login import (
    google_authorize,
    google_callback,
)
from cubeplex.api.routes.v1.sso import _enforce_forced_sso_for_user
from cubeplex.config import config
from cubeplex.models import Organization, SSOConnection, User
from cubeplex.sso.state import SSOStateStore

pytestmark = pytest.mark.asyncio


def _make_request(redis: fakeredis.aioredis.FakeRedis) -> Request:
    """Minimal Starlette Request whose app.state carries the fake redis."""

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


def _store(redis: fakeredis.aioredis.FakeRedis) -> SSOStateStore:
    return SSOStateStore(
        redis=redis,
        secret_key=config.get("auth.jwt_secret", "CHANGE_ME").encode(),
    )


# --- /google/authorize ------------------------------------------------------


async def test_authorize_404_when_disabled(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled or misconfigured → same 404 (no enumeration)."""
    # default config has enabled=false, but be explicit:
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.social_login._google_config",
        lambda: ("", False),
    )
    request = _make_request(fake_redis)
    with pytest.raises(HTTPException) as exc_info:
        await google_authorize(request)
    assert exc_info.value.status_code == 404


async def test_authorize_404_when_enabled_but_no_client_id(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.social_login._google_config",
        lambda: ("", True),
    )
    request = _make_request(fake_redis)
    with pytest.raises(HTTPException) as exc_info:
        await google_authorize(request)
    assert exc_info.value.status_code == 404


async def test_authorize_happy_path_builds_google_url(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.social_login._google_config",
        lambda: ("test-client-id.apps.googleusercontent.com", True),
    )
    request = _make_request(fake_redis)
    resp = await google_authorize(request)

    redirect_url = resp["redirect_url"]
    assert redirect_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    parsed = urlparse(redirect_url)
    qs = parse_qs(parsed.query)

    assert qs["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["openid email profile"]
    assert qs["redirect_uri"][0].endswith("/api/v1/auth/social/google/callback")
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]
    assert qs["state"][0]
    assert qs["nonce"][0]

    # State is consumable; PKCE verifier was attached; payload has the
    # right shape for the callback (protocol=google, no connection id).
    state = qs["state"][0]
    store = _store(fake_redis)
    verifier = await store.consume_pkce(state)
    assert verifier is not None
    payload = await store.consume(state)
    assert payload.protocol == "google"
    assert payload.sso_connection_id is None
    assert payload.org_id is None
    assert payload.nonce == qs["nonce"][0]


# --- /google/callback: state guards ----------------------------------------


async def test_callback_rejects_non_google_state(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """A state forged for the OIDC enterprise path must not pass the
    Google callback's protocol guard."""
    store = _store(fake_redis)
    bad_state = await store.issue(
        sso_connection_id="sso-fake",
        protocol="oidc",
        org_id="org-fake",
        oidc_nonce="n",
    )
    request = _make_request(fake_redis)
    with pytest.raises(HTTPException) as exc_info:
        await google_callback(
            code="any",
            state=bad_state,
            request=request,
            session=sso_session,
            user_manager=None,
        )
    assert exc_info.value.status_code == 400
    assert "google" in exc_info.value.detail.lower() or "invalid state" in exc_info.value.detail


async def test_callback_rejects_state_without_nonce(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """A google-protocol state with no nonce can't validate the ID token
    nonce claim → reject before touching Google."""
    store = _store(fake_redis)
    bad_state = await store.issue(
        sso_connection_id=None,
        protocol="google",
        org_id=None,
        oidc_nonce=None,
    )
    request = _make_request(fake_redis)
    with pytest.raises(HTTPException) as exc_info:
        await google_callback(
            code="any",
            state=bad_state,
            request=request,
            session=sso_session,
            user_manager=None,
        )
    assert exc_info.value.status_code == 400


async def test_callback_rejects_when_pkce_missing(
    sso_session: Any, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """Issuing the state without attach_pkce simulates a stolen state with
    no matching PKCE sidecar — must be refused before code exchange."""
    store = _store(fake_redis)
    state = await store.issue(
        sso_connection_id=None,
        protocol="google",
        org_id=None,
        oidc_nonce="n",
    )
    # Intentionally do NOT call attach_pkce.
    request = _make_request(fake_redis)
    with pytest.raises(HTTPException) as exc_info:
        await google_callback(
            code="any",
            state=state,
            request=request,
            session=sso_session,
            user_manager=None,
        )
    assert exc_info.value.status_code == 400
    assert "pkce" in exc_info.value.detail.lower()


# --- forced SSO blocks social login ----------------------------------------


async def test_forced_sso_blocks_google_login_for_member_of_sso_org(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """A user belonging to an org with active enterprise SSO must NOT be
    able to log in via Google — the social callback calls
    ``_enforce_forced_sso_for_user(allowed_org_id=None)`` and that must
    raise 403 ``sso_required``."""
    org, user = await make_org_with_user(email="member@enterprise.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="Enterprise OIDC",
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
