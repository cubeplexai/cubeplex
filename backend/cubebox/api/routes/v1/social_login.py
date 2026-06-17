"""Social login routes — Google OAuth2/OIDC.

Google is a well-known OIDC IdP. We reuse the OIDC validator (signature,
iss/aud/exp, nonce) by building an ephemeral ``OIDCConfig`` from app config
instead of persisting a transient ``SSOConnection`` row. The state token's
``protocol`` field is ``"google"`` and ``sso_connection_id`` is ``None`` —
the SSO enterprise callback rejects this combination by guard, and vice
versa.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.users import get_user_manager
from cubebox.config import config
from cubebox.db import get_session
from cubebox.mcp.oauth.pkce import generate_pkce
from cubebox.sso.identity import SSOLoginRejected, resolve_identity
from cubebox.sso.oidc import (
    OIDCConfig,
    OIDCUserInfo,
    OIDCValidationError,
    exchange_code,
)
from cubebox.sso.state import SSOStateExpired, SSOStateInvalid, SSOStateStore

router = APIRouter(prefix="/auth/social", tags=["social-login"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUER = "https://accounts.google.com"


def _get_state_store(request: Request) -> SSOStateStore:
    redis = request.app.state.redis
    secret = config.get("auth.jwt_secret", "CHANGE_ME").encode()
    return SSOStateStore(redis=redis, secret_key=secret)


def _base_url() -> str:
    return str(config.get("app.base_url", "http://localhost:3000")).rstrip("/")


def _google_config() -> tuple[str, bool]:
    """Return (client_id, enabled)."""
    enabled = config.get("social_login.google.enabled", False)
    client_id = config.get("social_login.google.client_id", "")
    return str(client_id or ""), bool(enabled)


def _build_google_oidc_config(client_id: str) -> OIDCConfig:
    return OIDCConfig(
        issuer=_GOOGLE_ISSUER,
        authorization_endpoint=_GOOGLE_AUTH_URL,
        token_endpoint=_GOOGLE_TOKEN_URL,
        userinfo_endpoint=_GOOGLE_USERINFO_URL,
        jwks_uri=_GOOGLE_JWKS_URI,
        client_id=client_id,
        scopes=("openid", "email", "profile"),
    )


@router.get("/google/authorize")
async def google_authorize(request: Request) -> dict[str, str]:
    client_id, enabled = _google_config()
    if not enabled or not client_id:
        # Same 404 whether disabled or misconfigured — no enumeration.
        raise HTTPException(404, detail="Google login not configured")

    store = _get_state_store(request)
    nonce = secrets.token_urlsafe(24)
    state = await store.issue(
        sso_connection_id=None,
        protocol="google",
        org_id=None,
        oidc_nonce=nonce,
    )
    pkce = generate_pkce()
    await store.attach_pkce(state=state, verifier=pkce.verifier)

    base = _base_url()
    redirect_uri = f"{base}/api/v1/auth/social/google/callback"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
    }
    return {"redirect_url": f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_manager: Annotated[Any, Depends(get_user_manager)],
) -> Response:
    store = _get_state_store(request)
    try:
        payload = await store.consume(state)
    except (SSOStateInvalid, SSOStateExpired) as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    if payload.protocol != "google" or payload.nonce is None:
        raise HTTPException(400, detail="invalid state payload for Google callback")

    verifier = await store.consume_pkce(state)
    if verifier is None:
        raise HTTPException(400, detail="pkce_verifier_missing")

    client_id, enabled = _google_config()
    if not enabled or not client_id:
        raise HTTPException(404, detail="Google login not configured")

    client_secret = await _get_google_client_secret(request, session)
    base = _base_url()
    redirect_uri = f"{base}/api/v1/auth/social/google/callback"

    google_cfg = _build_google_oidc_config(client_id)

    try:
        userinfo: OIDCUserInfo = await exchange_code(
            google_cfg,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
            client_secret=client_secret,
            expected_nonce=payload.nonce,
        )
    except OIDCValidationError as exc:
        raise HTTPException(400, detail=f"id_token_validation_failed: {exc}") from exc

    try:
        result = await resolve_identity(
            session,
            user_manager=user_manager,
            provider_type="google",
            provider_id="google",
            external_id=userinfo.sub,
            external_email=userinfo.email,
            email_verified=userinfo.email_verified,
            claims=userinfo.claims or {},
        )
    except SSOLoginRejected as exc:
        raise HTTPException(403, detail=exc.code) from exc

    # Forced-SSO must also block social login: if the resolved user
    # belongs to any org with active enterprise SSO, refuse.
    from cubebox.api.routes.v1.sso import (
        _enforce_forced_sso_for_user,
        _login_and_redirect,
    )

    await _enforce_forced_sso_for_user(session, result.user, allowed_org_id=None)
    return await _login_and_redirect(request, session, result.user)


async def _get_google_client_secret(request: Request, session: AsyncSession) -> str:
    """Decrypt the Google client_secret from the credential vault.

    The Google client_secret is a *system* credential — it lives at the
    deployment level, not per-org. Stored with ``kind="social_login"``,
    ``name="google"``, and ``org_id=None`` (system row via the partial
    unique index on the credential table).
    """
    from cubebox.credentials.encryption import EncryptionBackend
    from cubebox.repositories.credential import CredentialRepository

    repo = CredentialRepository(session, org_id=None)
    cred = await repo.get_by_kind_name(kind="social_login", name="google")
    if cred is None:
        raise HTTPException(500, detail="Google client_secret not configured in vault")
    backend: EncryptionBackend = request.app.state.encryption_backend
    plaintext = await backend.decrypt(cred.value_encrypted)
    return plaintext.decode()
