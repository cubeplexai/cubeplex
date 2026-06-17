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
        try:
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
        except httpx.HTTPStatusError as exc:
            # Expired authorization codes, replay, IdP outages — translate
            # so the callback returns a clean 400 instead of an opaque 500.
            raise OIDCValidationError(f"token_endpoint_status_{exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise OIDCValidationError("token_endpoint_unreachable") from exc
        token_data = token_resp.json()
        id_token = token_data.get("id_token")
        if not id_token:
            raise OIDCValidationError("missing_id_token")

        try:
            jwks_resp = await http.get(cfg.jwks_uri, timeout=10)
            jwks_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OIDCValidationError(f"jwks_status_{exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise OIDCValidationError("jwks_unreachable") from exc
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

        # Start from the signed ID-token claims. The userinfo endpoint
        # response is NOT signed; if we replaced claims with it wholesale,
        # an IdP whose ID token says email_verified=true would silently
        # become email_verified=false (or vice versa) based on whatever
        # the userinfo body — or a MITM of the userinfo TLS — returns.
        # So userinfo only augments fields the ID token didn't carry; the
        # security-sensitive claims (sub, email, email_verified, aud, iss)
        # never get overwritten.
        userinfo: dict[str, Any] = dict(claims)
        if cfg.userinfo_endpoint:
            access_token = token_data["access_token"]
            try:
                userinfo_resp = await http.get(
                    cfg.userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo_resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise OIDCValidationError(f"userinfo_status_{exc.response.status_code}") from exc
            except httpx.RequestError as exc:
                raise OIDCValidationError("userinfo_unreachable") from exc
            ui = userinfo_resp.json()
            if ui.get("sub") != claims["sub"]:
                raise OIDCValidationError("userinfo_sub_mismatch")
            for key, value in ui.items():
                userinfo.setdefault(key, value)

    return OIDCUserInfo(
        sub=str(userinfo.get("sub", "")),
        email=str(userinfo.get("email", "")),
        email_verified=bool(userinfo.get("email_verified", False)),
        name=userinfo.get("name"),
        claims=userinfo,
    )


class OIDCDiscoveryRefused(Exception):
    """The supplied issuer URL is not a safe discovery target."""


def _refuse_ssrf_target(url: str) -> None:
    """Reject issuer URLs that would let an authenticated admin probe the
    internal network or non-HTTP services (SSRF guard).

    - Require https:// (http would let an admin probe http-only metadata
      services like 169.254.169.254 with predictable timing).
    - Reject hostnames that resolve into private / loopback / link-local
      ranges so an admin cannot scan the deployment's internal hosts.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise OIDCDiscoveryRefused("scheme_must_be_https")
    host = parsed.hostname or ""
    if not host:
        raise OIDCDiscoveryRefused("missing_host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise OIDCDiscoveryRefused("dns_lookup_failed") from exc
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise OIDCDiscoveryRefused("private_address_blocked")


async def discover_oidc_endpoints(issuer_url: str) -> dict[str, Any]:
    """Fetch .well-known/openid-configuration for an issuer.

    Refuses non-https and any host whose DNS resolves to private / loopback /
    link-local ranges — without this guard an org admin could use the
    admin discover endpoint to scan the deployment's internal network.
    """
    base = issuer_url.rstrip("/")
    _refuse_ssrf_target(base)
    url = base + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(follow_redirects=False) as http:
        resp = await http.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
