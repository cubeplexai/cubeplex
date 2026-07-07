"""Complete the four-layer OAuth handshake.

Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §6.
The /api/v1/oauth/mcp/callback route delegates to ``OAuthCallbackHandler``,
which:

1. Consumes the state token (one-shot via ``OAuthStateStore.consume``).
2. Reads the PKCE verifier (one-shot via ``OAuthStateStore.consume_pkce``).
3. POSTs to the AS token endpoint with code + verifier.
4. Encrypts the access token (and refresh token, if present) into the vault.
5. Upserts an MCPCredentialGrant at the scope the state token committed to,
   pointing at the new credential ids.
6. Updates ``install.auth_status`` from 'pending' → 'authorized' iff the new
   grant's scope matches the install's currently-effective required scope.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.credentials.dependencies import build_credential_service
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
)
from cubebox.mcp.exceptions import OAuthStateExpired, OAuthStateInvalid
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.state import OAuthStatePayload, OAuthStateStore
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models.mcp import MCPConnectorInstall, MCPCredentialGrant
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPCredentialGrantRepository,
)
from cubebox.services.credential import CredentialService


@dataclass(frozen=True)
class OAuthCallbackResult:
    """Return shape that the route serializes into the redirect query string."""

    status: Literal["ok", "error", "cancelled"]
    install_id: str  # may be empty string when state could not be decoded
    state: str  # the original state token; required so the parent can match
    reason: str | None = None
    frontend_origin: str | None = None


class OAuthCallbackHandler:
    """Per-request handler.

    Repos and credential service are built INSIDE ``handle_callback()`` after
    the state token reveals the install_id (and therefore org_id) — the
    callback route is unauthenticated and has no request_context to seed an
    org-scoped factory.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        backend: EncryptionBackend,
        state_store: OAuthStateStore,
        metadata: OAuthMetadataDiscovery,
        http_client: httpx.AsyncClient,
        signer: MCPUserTokenSigner,
        redis: Redis,
    ) -> None:
        self._session = session
        self._backend = backend
        self._state_store = state_store
        self._metadata = metadata
        self._http = http_client
        # Held for post-grant discovery: an org-scoped OAuthTokenManager
        # and the identity-token signer are needed to drive
        # ``_resolve_headers_from_spec``. We build the token manager
        # lazily inside ``handle_callback`` because org_id only becomes
        # known after the install row is loaded.
        self._signer = signer
        self._redis = redis

    async def handle_callback(
        self,
        *,
        state: str,
        code: str | None,
        error: str | None = None,
    ) -> OAuthCallbackResult:
        # AS reported error directly (user_denied / invalid_request / ...).
        if error is not None and code is None:
            try:
                payload = await self._state_store.consume(state)
            except (OAuthStateInvalid, OAuthStateExpired):
                return OAuthCallbackResult(
                    status="error", install_id="", state=state, reason="state_invalid"
                )
            return OAuthCallbackResult(
                status="cancelled" if error == "access_denied" else "error",
                install_id=payload.install_id,
                state=state,
                reason=error,
                frontend_origin=payload.frontend_origin,
            )

        if code is None:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="missing_code"
            )

        try:
            payload = await self._state_store.consume(state)
        except OAuthStateExpired:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="state_expired"
            )
        except OAuthStateInvalid:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="state_invalid"
            )

        verifier = await self._state_store.consume_pkce(state)
        if verifier is None:
            return OAuthCallbackResult(
                status="error",
                install_id=payload.install_id,
                state=state,
                reason="pkce_missing",
                frontend_origin=payload.frontend_origin,
            )

        # We need org-scoped repos to honor multi-tenant isolation, but we
        # don't know org_id until we read the install row — and the install
        # repo itself is org-scoped. One-off org-agnostic read on the raw
        # model first.
        install = (
            await self._session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.id == payload.install_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if install is None:
            return OAuthCallbackResult(
                status="error",
                install_id=payload.install_id,
                state=state,
                reason="install_not_found",
                frontend_origin=payload.frontend_origin,
            )

        # Now build the org-scoped service surface for the rest of the work.
        cred_service = build_credential_service(
            self._session,
            self._backend,
            org_id=install.org_id,
            actor_user_id=payload.actor_user_id,
        )
        install_repo = MCPConnectorInstallRepository(self._session, org_id=install.org_id)
        grant_repo = MCPCredentialGrantRepository(self._session, org_id=install.org_id)

        try:
            token = await self._post_token_exchange(
                install, code, verifier, cred_service, payload.frontend_origin
            )
        except httpx.HTTPError as exc:
            return OAuthCallbackResult(
                status="error",
                install_id=install.id,
                state=state,
                reason=f"token_exchange_failed:{exc.__class__.__name__}",
                frontend_origin=payload.frontend_origin,
            )

        grant = await self._upsert_grant(
            install=install,
            payload=payload,
            token=token,
            cred_service=cred_service,
            grant_repo=grant_repo,
        )
        await self._maybe_authorize_install(
            install=install,
            grant=grant,
            install_repo=install_repo,
        )

        # Validate the freshly-granted token against the real MCP
        # server by running discovery. A successful exchange against
        # the AS doesn't prove the server accepts the token — only an
        # actual ``tools/list`` does. Failures land in
        # install.discovery_status / last_error.
        # Local import: ``mcp_discovery`` pulls in cubepi_runtime which
        # transitively imports OAuthTokenManager via effective.py, so
        # importing it at module top would create a circular import
        # through ``cubebox.mcp.oauth.__init__``.
        from cubebox.services.mcp_discovery import run_post_grant_discovery

        token_mgr = OAuthTokenManager(
            http_client=self._http,
            redis=self._redis,
            encryption_backend=self._backend,
            credential_repo=CredentialRepository(self._session, org_id=install.org_id),
            metadata=self._metadata,
        )
        await run_post_grant_discovery(
            install_id=install.id,
            workspace_id=payload.workspace_id,
            actor_user_id=payload.actor_user_id,
            session=self._session,
            cred_service=cred_service,
            signer=self._signer,
            token_mgr=token_mgr,
        )

        return OAuthCallbackResult(
            status="ok",
            install_id=install.id,
            state=state,
            frontend_origin=payload.frontend_origin,
        )

    async def _post_token_exchange(
        self,
        install: MCPConnectorInstall,
        code: str,
        verifier: str,
        cred_service: CredentialService,
        frontend_origin: str | None = None,
    ) -> dict[str, Any]:
        # Token endpoint lives in AS metadata, not on the install row.
        _pr, as_meta = await self._metadata.discover_for_resource(install.server_url)
        client_id = install.oauth_client_config.get("client_id")
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(frontend_origin),
                "client_id": client_id,
                "code_verifier": verifier,
                # RFC 8707 audience binding — must match the `resource`
                # sent on the authorize request (see start.py
                # _build_authorize_url). MCP authorization spec
                # requires the same target on both legs of the flow.
                "resource": install.server_url,
            }
        )
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        secret_id = install.oauth_client_config.get("client_secret_credential_id")
        if isinstance(secret_id, str) and secret_id:
            secret = await cred_service.get_decrypted(
                credential_id=secret_id,
                requesting_kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
            )
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"
        resp = await self._http.post(as_meta.token_endpoint, content=body, headers=headers)
        resp.raise_for_status()
        result = resp.json()
        assert isinstance(result, dict)
        return result

    async def _upsert_grant(
        self,
        *,
        install: MCPConnectorInstall,
        payload: OAuthStatePayload,
        token: dict[str, Any],
        cred_service: CredentialService,
        grant_repo: MCPCredentialGrantRepository,
    ) -> MCPCredentialGrant:
        # Credential name encodes the full grant identity so distinct grants
        # (e.g. two workspaces' grants under the same org install) do not
        # collide on the unique ``(org_id, kind, name)`` index. Re-OAuth on
        # the same grant identity rotates in place.
        grant_name_suffix = _grant_credential_suffix(payload)
        assert payload.grant_scope is not None
        access_id = await cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name=f"mcp:{install.id}:{grant_name_suffix}:access",
            plaintext=str(token["access_token"]),
        )
        refresh_id: str | None = None
        if "refresh_token" in token:
            refresh_id = await cred_service.upsert_by_kind_name(
                kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
                name=f"mcp:{install.id}:{grant_name_suffix}:refresh",
                plaintext=str(token["refresh_token"]),
            )
        expires_at: datetime | None = None
        if "expires_in" in token:
            expires_at = datetime.now(tz=UTC) + timedelta(seconds=int(token["expires_in"]))

        existing = await grant_repo.get_for_scope(
            install_id=install.id,
            grant_scope=payload.grant_scope,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
        )
        if existing is None:
            grant = MCPCredentialGrant(
                org_id=install.org_id,
                install_id=install.id,
                grant_scope=payload.grant_scope,
                workspace_id=payload.workspace_id,
                user_id=payload.user_id,
                credential_id=access_id,
                refresh_credential_id=refresh_id,
                expires_at=expires_at,
                grant_status="valid",
                created_by_user_id=payload.actor_user_id,
            )
            return await grant_repo.add(grant)
        existing.credential_id = access_id
        # Only overwrite refresh_credential_id when the AS sent a new
        # refresh_token. Many providers (GitHub, Slack, Google with
        # access_type=online, ...) omit the refresh_token on
        # re-authorization unless the user fully re-consents — they
        # expect the client to keep the original refresh credential.
        # Blindly assigning `None` here would convert a refreshable
        # grant into a non-refreshable one on the next silent re-auth,
        # and the connector would break the first time the access
        # token expired.
        if refresh_id is not None:
            existing.refresh_credential_id = refresh_id
        existing.expires_at = expires_at
        existing.grant_status = "valid"
        return await grant_repo.update(existing)

    async def _maybe_authorize_install(
        self,
        *,
        install: MCPConnectorInstall,
        grant: MCPCredentialGrant,
        install_repo: MCPConnectorInstallRepository,
    ) -> None:
        """Flip ``auth_status`` only when the scope matches the required policy.

        For org/workspace policies the install becomes 'authorized' as soon as
        a grant at the matching scope lands. For user policy, every member has
        their own grant — ``auth_status`` stays 'pending' (per-install bit,
        not per-user).
        """
        required_scope = install.default_credential_policy
        if required_scope == grant.grant_scope and required_scope in {"org", "workspace"}:
            install.auth_status = "authorized"
            await install_repo.update(install)


def _redirect_uri(frontend_origin: str | None = None) -> str:
    if frontend_origin:
        return f"{frontend_origin.rstrip('/')}/api/v1/oauth/mcp/callback"
    base = str(config.get("public_base_url", "http://localhost:8000")).rstrip("/")
    return f"{base}/api/v1/oauth/mcp/callback"


def _grant_credential_suffix(payload: OAuthStatePayload) -> str:
    """Stable, scope-aware suffix so each grant owns distinct credential
    rows on the unique ``(org_id, kind, name)`` index. Re-OAuth for the SAME
    grant identity reuses the same suffix and rotates in place.
    """
    if payload.grant_scope == "org":
        return "org"
    if payload.grant_scope == "workspace":
        return f"ws:{payload.workspace_id}"
    # user
    return f"usr:{payload.user_id}:ws:{payload.workspace_id}"
