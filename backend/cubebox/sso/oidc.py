"""OIDC client — build authorize URL, exchange code, validate ID token."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey
from authlib.jose import jwt as jose_jwt
from authlib.jose.errors import JoseError

from cubebox.models.sso_connection import SSOConnection


class OIDCValidationError(Exception):
    """Raised when ID token validation (sig/iss/aud/exp/nonce) fails."""


@dataclass(frozen=True)
class OIDCConfig:
    """Minimal OIDC IdP config — usable by both enterprise SSO (built from
    a persisted ``SSOConnection.config``) and Google social login (built
    from the cubebox app config). The OIDC client takes this dataclass so
    the social-login path never has to fake an ORM row."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_id: str
    userinfo_endpoint: str | None = None
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    attribute_mapping: dict[str, str] | None = None


def oidc_config_from_connection(connection: SSOConnection) -> OIDCConfig:
    """Build an OIDCConfig from a persisted SSOConnection's JSONB config."""
    cfg = connection.config
    return OIDCConfig(
        issuer=cfg["issuer"],
        authorization_endpoint=cfg["authorization_endpoint"],
        token_endpoint=cfg["token_endpoint"],
        jwks_uri=cfg["jwks_uri"],
        client_id=cfg["client_id"],
        userinfo_endpoint=cfg.get("userinfo_endpoint"),
        scopes=tuple(cfg.get("scopes", ["openid", "email", "profile"])),
        attribute_mapping=cfg.get("attribute_mapping"),
    )


@dataclass(frozen=True)
class OIDCUserInfo:
    sub: str
    email: str
    email_verified: bool
    name: str | None = None
    claims: dict[str, Any] | None = None


def build_authorize_url(
    cfg: OIDCConfig,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build the IdP authorization URL for an OIDC config.

    ``nonce`` MUST be a random value bound to this authorize request
    (typically stored in the signed state payload). The callback handler
    re-verifies it against the ID token's ``nonce`` claim.
    """
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{cfg.authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    cfg: OIDCConfig,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_secret: str,
    expected_nonce: str,
    clock_skew_seconds: int = 60,
) -> OIDCUserInfo:
    """Exchange authorization code for tokens, validate the ID token, then
    fetch userinfo.

    Validation steps (all mandatory):
      1. JWS signature against the connection's ``jwks_uri``.
      2. ``iss`` matches the connection's configured issuer.
      3. ``aud`` contains the configured client_id.
      4. ``exp`` is in the future, ``iat`` within ``clock_skew_seconds``.
      5. ``nonce`` matches ``expected_nonce``.
      6. If a userinfo_endpoint is configured, fetch it and require
         ``userinfo.sub == id_token.sub``.
    """
    async with httpx.AsyncClient() as http:
        token_resp = await http.post(
            cfg.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": cfg.client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        id_token = token_data.get("id_token")
        if not id_token:
            raise OIDCValidationError("missing_id_token")

        jwks_resp = await http.get(cfg.jwks_uri, timeout=10)
        jwks_resp.raise_for_status()
        jwks = JsonWebKey.import_key_set(jwks_resp.json())

        try:
            claims = jose_jwt.decode(
                id_token,
                jwks,
                claims_options={
                    "iss": {"essential": True, "value": cfg.issuer},
                    "aud": {"essential": True, "values": [cfg.client_id]},
                    "exp": {"essential": True},
                    "nonce": {"essential": True, "value": expected_nonce},
                },
            )
            claims.validate(now=int(time.time()), leeway=clock_skew_seconds)
        except JoseError as exc:
            raise OIDCValidationError(f"id_token_invalid: {exc}") from exc

        userinfo: dict[str, Any] = dict(claims)
        if cfg.userinfo_endpoint:
            access_token = token_data["access_token"]
            userinfo_resp = await http.get(
                cfg.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            ui = userinfo_resp.json()
            if ui.get("sub") != claims["sub"]:
                raise OIDCValidationError("userinfo_sub_mismatch")
            userinfo = ui

    return OIDCUserInfo(
        sub=str(userinfo.get("sub", "")),
        email=str(userinfo.get("email", "")),
        email_verified=bool(userinfo.get("email_verified", False)),
        name=userinfo.get("name"),
        claims=userinfo,
    )


async def discover_oidc_endpoints(issuer_url: str) -> dict[str, Any]:
    """Fetch .well-known/openid-configuration for an issuer."""
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
