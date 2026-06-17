"""Unit tests for cubebox.sso.oidc."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.jose import JsonWebKey
from authlib.jose import jwt as jose_jwt

from cubebox.models.sso_connection import SSOConnection
from cubebox.sso.oidc import (
    OIDCConfig,
    OIDCUserInfo,
    OIDCValidationError,
    build_authorize_url,
    discover_oidc_endpoints,
    exchange_code,
    oidc_config_from_connection,
)

ISSUER = "https://idp.example.com"
CLIENT_ID = "client-abc"
CLIENT_SECRET = "client-secret"
REDIRECT_URI = "https://app.example.com/auth/sso/callback"
TOKEN_ENDPOINT = "https://idp.example.com/token"
JWKS_URI = "https://idp.example.com/jwks"
AUTHZ_ENDPOINT = "https://idp.example.com/authorize"
USERINFO_ENDPOINT = "https://idp.example.com/userinfo"


@pytest.fixture
def rsa_key() -> Any:
    return JsonWebKey.generate_key("RSA", 2048, options={"kid": "test-kid"}, is_private=True)


def _jwks(key: Any) -> dict[str, Any]:
    return {"keys": [key.as_dict(is_private=False)]}


def _id_token(
    key: Any,
    *,
    iss: str = ISSUER,
    aud: str | list[str] = CLIENT_ID,
    sub: str = "user-1",
    nonce: str = "nonce-xyz",
    email: str = "user@example.com",
    email_verified: bool = True,
    name: str | None = "User One",
    exp_offset: int = 600,
    iat_offset: int = 0,
) -> str:
    header = {"alg": "RS256", "kid": "test-kid"}
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "nonce": nonce,
        "email": email,
        "email_verified": email_verified,
        "exp": now + exp_offset,
        "iat": now + iat_offset,
    }
    if name is not None:
        payload["name"] = name
    tok = jose_jwt.encode(header, payload, key)
    return tok.decode("ascii") if isinstance(tok, bytes) else str(tok)


def _make_transport(
    *,
    rsa_key: Any,
    id_token: str | None = None,
    token_status: int = 200,
    userinfo_body: dict[str, Any] | None = None,
    userinfo_status: int = 200,
    discovery_body: dict[str, Any] | None = None,
) -> httpx.MockTransport:
    """Mock the IdP endpoints used by the OIDC client."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_ENDPOINT:
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "fail"})
            body = {
                "access_token": "acc-tok",
                "token_type": "Bearer",
            }
            if id_token is not None:
                body["id_token"] = id_token
            return httpx.Response(200, json=body)
        if url == JWKS_URI:
            return httpx.Response(200, json=_jwks(rsa_key))
        if url == USERINFO_ENDPOINT:
            return httpx.Response(
                userinfo_status,
                json=userinfo_body or {"sub": "user-1", "email": "user@example.com"},
            )
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=discovery_body or {})
        return httpx.Response(404, json={"error": "unhandled", "url": url})

    return httpx.MockTransport(handler)


@pytest.fixture
def patch_async_client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Monkeypatch httpx.AsyncClient so it injects a MockTransport."""

    def _patch(transport: httpx.MockTransport) -> None:
        original = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr("cubebox.sso.oidc.httpx.AsyncClient", factory)

    return _patch


def _base_cfg() -> OIDCConfig:
    return OIDCConfig(
        issuer=ISSUER,
        authorization_endpoint=AUTHZ_ENDPOINT,
        token_endpoint=TOKEN_ENDPOINT,
        jwks_uri=JWKS_URI,
        client_id=CLIENT_ID,
        userinfo_endpoint=USERINFO_ENDPOINT,
    )


# ----------------------------------------------------------------------
# build_authorize_url
# ----------------------------------------------------------------------


def test_build_authorize_url_contains_all_required_params() -> None:
    cfg = _base_cfg()
    url = build_authorize_url(
        cfg,
        redirect_uri=REDIRECT_URI,
        state="st-123",
        nonce="n-456",
        code_challenge="cc-789",
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHZ_ENDPOINT
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == [CLIENT_ID]
    assert qs["redirect_uri"] == [REDIRECT_URI]
    assert qs["scope"] == ["openid email profile"]
    assert qs["state"] == ["st-123"]
    assert qs["nonce"] == ["n-456"]
    assert qs["code_challenge"] == ["cc-789"]
    assert qs["code_challenge_method"] == ["S256"]


def test_build_authorize_url_respects_custom_scopes() -> None:
    cfg = OIDCConfig(
        issuer=ISSUER,
        authorization_endpoint=AUTHZ_ENDPOINT,
        token_endpoint=TOKEN_ENDPOINT,
        jwks_uri=JWKS_URI,
        client_id=CLIENT_ID,
        scopes=("openid", "email", "groups"),
    )
    url = build_authorize_url(
        cfg,
        redirect_uri=REDIRECT_URI,
        state="s",
        nonce="n",
        code_challenge="c",
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["scope"] == ["openid email groups"]


# ----------------------------------------------------------------------
# oidc_config_from_connection
# ----------------------------------------------------------------------


def test_oidc_config_from_connection_maps_fields() -> None:
    conn = SSOConnection(
        org_id="org-test",
        protocol="oidc",
        display_name="Acme OIDC",
        status="active",
        provisioning="auto",
        config={
            "issuer": ISSUER,
            "authorization_endpoint": AUTHZ_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "client_id": CLIENT_ID,
            "userinfo_endpoint": USERINFO_ENDPOINT,
            "scopes": ["openid", "email"],
            "attribute_mapping": {"email": "preferred_email"},
        },
    )
    cfg = oidc_config_from_connection(conn)
    assert cfg.issuer == ISSUER
    assert cfg.authorization_endpoint == AUTHZ_ENDPOINT
    assert cfg.token_endpoint == TOKEN_ENDPOINT
    assert cfg.jwks_uri == JWKS_URI
    assert cfg.client_id == CLIENT_ID
    assert cfg.userinfo_endpoint == USERINFO_ENDPOINT
    assert cfg.scopes == ("openid", "email")
    assert cfg.attribute_mapping == {"email": "preferred_email"}


def test_oidc_config_from_connection_defaults_scopes_when_missing() -> None:
    conn = SSOConnection(
        org_id="org-test",
        protocol="oidc",
        display_name="Acme",
        config={
            "issuer": ISSUER,
            "authorization_endpoint": AUTHZ_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "client_id": CLIENT_ID,
        },
    )
    cfg = oidc_config_from_connection(conn)
    assert cfg.scopes == ("openid", "email", "profile")
    assert cfg.userinfo_endpoint is None
    assert cfg.attribute_mapping is None


# ----------------------------------------------------------------------
# exchange_code — happy path
# ----------------------------------------------------------------------


async def test_exchange_code_happy_path(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    nonce = "nonce-xyz"
    token = _id_token(rsa_key, nonce=nonce, sub="user-1")
    patch_async_client(
        _make_transport(
            rsa_key=rsa_key,
            id_token=token,
            userinfo_body={
                "sub": "user-1",
                "email": "user@example.com",
                "email_verified": True,
                "name": "User One",
            },
        )
    )
    info = await exchange_code(
        _base_cfg(),
        code="authcode",
        redirect_uri=REDIRECT_URI,
        code_verifier="ver",
        client_secret=CLIENT_SECRET,
        expected_nonce=nonce,
    )
    assert isinstance(info, OIDCUserInfo)
    assert info.sub == "user-1"
    assert info.email == "user@example.com"
    assert info.email_verified is True
    assert info.name == "User One"
    assert info.claims is not None and info.claims["sub"] == "user-1"


async def test_exchange_code_without_userinfo_endpoint_uses_id_token(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    nonce = "n-no-ui"
    token = _id_token(rsa_key, nonce=nonce, sub="user-2", email="u2@example.com")
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=token))
    cfg = OIDCConfig(
        issuer=ISSUER,
        authorization_endpoint=AUTHZ_ENDPOINT,
        token_endpoint=TOKEN_ENDPOINT,
        jwks_uri=JWKS_URI,
        client_id=CLIENT_ID,
        userinfo_endpoint=None,
    )
    info = await exchange_code(
        cfg,
        code="c",
        redirect_uri=REDIRECT_URI,
        code_verifier="v",
        client_secret=CLIENT_SECRET,
        expected_nonce=nonce,
    )
    assert info.sub == "user-2"
    assert info.email == "u2@example.com"


# ----------------------------------------------------------------------
# exchange_code — failure modes
# ----------------------------------------------------------------------


async def test_exchange_code_missing_id_token(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=None))
    with pytest.raises(OIDCValidationError, match="missing_id_token"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="n",
        )


async def test_exchange_code_rejects_mismatched_nonce(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    token = _id_token(rsa_key, nonce="actual-nonce")
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=token))
    with pytest.raises(OIDCValidationError, match="id_token_invalid"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="expected-different",
        )


async def test_exchange_code_rejects_wrong_iss(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    token = _id_token(rsa_key, iss="https://attacker.example", nonce="n1")
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=token))
    with pytest.raises(OIDCValidationError, match="id_token_invalid"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="n1",
        )


async def test_exchange_code_rejects_wrong_aud(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    token = _id_token(rsa_key, aud="other-client", nonce="n1")
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=token))
    with pytest.raises(OIDCValidationError, match="id_token_invalid"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="n1",
        )


async def test_exchange_code_rejects_expired_token(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    # exp 1 hour in the past, well beyond the default 60s skew.
    token = _id_token(rsa_key, nonce="n1", exp_offset=-3600, iat_offset=-7200)
    patch_async_client(_make_transport(rsa_key=rsa_key, id_token=token))
    with pytest.raises(OIDCValidationError, match="id_token_invalid"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="n1",
        )


async def test_exchange_code_rejects_userinfo_sub_mismatch(
    rsa_key: Any, patch_async_client: Callable[..., None]
) -> None:
    token = _id_token(rsa_key, nonce="n1", sub="user-1")
    patch_async_client(
        _make_transport(
            rsa_key=rsa_key,
            id_token=token,
            userinfo_body={"sub": "user-2", "email": "evil@example.com"},
        )
    )
    with pytest.raises(OIDCValidationError, match="userinfo_sub_mismatch"):
        await exchange_code(
            _base_cfg(),
            code="c",
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
            client_secret=CLIENT_SECRET,
            expected_nonce="n1",
        )


# ----------------------------------------------------------------------
# discover_oidc_endpoints
# ----------------------------------------------------------------------


async def test_discover_oidc_endpoints_parses_json(
    rsa_key: Any,
    patch_async_client: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHZ_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "userinfo_endpoint": USERINFO_ENDPOINT,
    }
    patch_async_client(_make_transport(rsa_key=rsa_key, discovery_body=body))
    # Bypass the SSRF guard's DNS lookup so the mock can answer.
    monkeypatch.setattr("cubebox.sso.oidc._refuse_ssrf_target", lambda url: None)
    out = await discover_oidc_endpoints(ISSUER + "/")
    assert out == body
    assert json.dumps(out)  # round-trippable
