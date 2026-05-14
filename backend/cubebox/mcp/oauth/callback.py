"""OAuth authorization-code callback handler.

Given the ``state`` and ``code`` query parameters returned by the AS, this
handler:

1. Verifies + consumes the HMAC-signed state token (one-shot via redis).
2. Looks up the install (``MCPServer``) referenced by the state payload.
3. Reads + deletes the PKCE ``code_verifier`` from redis (one-shot, key
   ``mcp_oauth_pkce:{install_id}`` — populated by the ``/oauth/start`` route
   that Phase 5 adds).
4. POSTs ``grant_type=authorization_code`` to the AS token endpoint.
5. On 200, persists the access + refresh tokens in the credential vault and
   wires them onto the right scope (``MCPServer.credential_id`` for org or
   ``user_mcp_credentials`` for user); flips ``authed=True``; runs an initial
   tool discovery so the install is immediately usable.

Failure paths:

- Invalid state         → ``OAuthStateInvalid`` / ``OAuthStateExpired``
- Missing PKCE verifier → ``OAuthPKCEMissing``
- Server lookup fails   → ``OAuthInvalidServerState``
- AS returns non-2xx    → ``OAuthCallbackError``
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import httpx
from redis.asyncio import Redis

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
)
from cubebox.mcp.exceptions import (
    OAuthCallbackError,
    OAuthInvalidServerState,
    OAuthPKCEMissing,
)
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.mcp.oauth.token_manager import _compute_expires_at
from cubebox.models import MCPServer, UserMCPCredential
from cubebox.repositories.mcp import MCPServerRepository, UserMCPCredentialRepository
from cubebox.services.credential import CredentialService
from cubebox.utils.time import utc_isoformat

PKCE_REDIS_KEY_PREFIX = "mcp_oauth_pkce:"


@dataclass(frozen=True)
class CallbackResult:
    """Outcome of a successful OAuth callback exchange."""

    install_id: str
    authed: bool


# Factory signature: ``(org_id, actor_user_id) -> CredentialService``.
CredentialServiceFactory = Callable[[str | None, str | None], CredentialService]


class OAuthCallbackHandler:
    """Handle the AS redirect_uri hit and finalize the install."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        redis: Redis,
        state_store: OAuthStateStore,
        metadata: OAuthMetadataDiscovery,
        encryption_backend: EncryptionBackend,
        credential_service_factory: CredentialServiceFactory,
        server_repo: MCPServerRepository,
        user_cred_repo: UserMCPCredentialRepository,
        redirect_uri: str,
    ) -> None:
        self._http = http_client
        self._redis = redis
        self._state_store = state_store
        self._metadata = metadata
        self._backend = encryption_backend
        self._cred_service_factory = credential_service_factory
        self._server_repo = server_repo
        self._user_cred_repo = user_cred_repo
        self._redirect_uri = redirect_uri

    async def handle_callback(
        self,
        *,
        state: str,
        code: str,
        expected_actor_user_id: str | None = None,
    ) -> CallbackResult:
        payload = await self._state_store.consume(state)

        if expected_actor_user_id is not None and payload.actor_user_id != expected_actor_user_id:
            # The browser cookie ticket and the HMAC-signed state both carry
            # the actor identity. A mismatch means the cookie was paired with
            # a state token from a different login session — refuse to
            # finalize the install.
            raise OAuthInvalidServerState("OAuth callback actor mismatch between state and ticket")

        server = await self._server_repo.get(payload.install_id)
        if server is None or server.auth_method != "oauth":
            raise OAuthInvalidServerState(f"install {payload.install_id} is not an OAuth install")

        code_verifier = await self._consume_pkce(payload.install_id)

        client_config = dict(server.oauth_client_config or {})
        client_id = client_config.get("client_id")
        if not isinstance(client_id, str):
            raise OAuthInvalidServerState(
                f"server {server.id} oauth_client_config has no client_id"
            )

        token_response = await self._exchange_authorization_code(
            server=server,
            client_config=client_config,
            client_id=client_id,
            code=code,
            code_verifier=code_verifier,
        )

        await self._persist_tokens(
            server=server,
            actor_user_id=payload.actor_user_id,
            client_config=client_config,
            token_response=token_response,
        )

        return CallbackResult(install_id=server.id, authed=True)

    # ---------------- PKCE replay ---------------- #

    async def _consume_pkce(self, install_id: str) -> str:
        key = PKCE_REDIS_KEY_PREFIX + install_id
        verifier = await self._redis.get(key)
        if verifier is None:
            raise OAuthPKCEMissing(
                f"no PKCE verifier in redis for install {install_id} (expired or never set)"
            )
        await self._redis.delete(key)
        if isinstance(verifier, bytes):
            verifier = verifier.decode("utf-8")
        return cast(str, verifier)

    # ---------------- token exchange ---------------- #

    async def _exchange_authorization_code(
        self,
        *,
        server: MCPServer,
        client_config: dict[str, Any],
        client_id: str,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        _, as_meta = await self._metadata.discover_for_resource(server.server_url)

        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "code_verifier": code_verifier,
            "client_id": client_id,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        client_secret_credential_id = client_config.get("client_secret_credential_id")
        if isinstance(client_secret_credential_id, str):
            cred_service = self._cred_service_factory(server.org_id, None)
            client_secret = await cred_service.get_decrypted(
                credential_id=client_secret_credential_id,
                requesting_kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
            )
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"

        response = await self._http.post(as_meta.token_endpoint, data=data, headers=headers)

        if response.status_code != 200:
            error: str | None = None
            error_description: str | None = None
            try:
                err_body = response.json()
                if isinstance(err_body, dict):
                    error = _opt_str(err_body.get("error"))
                    error_description = _opt_str(err_body.get("error_description"))
            except ValueError:
                pass
            raise OAuthCallbackError(
                response.status_code,
                error=error,
                error_description=error_description,
            )

        body = response.json()
        if not isinstance(body, dict) or "access_token" not in body:
            raise OAuthCallbackError(
                response.status_code,
                error="invalid_response",
                error_description="token response missing access_token",
            )
        return cast(dict[str, Any], body)

    # ---------------- vault persistence ---------------- #

    async def _persist_tokens(
        self,
        *,
        server: MCPServer,
        actor_user_id: str,
        client_config: dict[str, Any],
        token_response: dict[str, Any],
    ) -> None:
        access_token = str(token_response["access_token"])
        refresh_token_raw = token_response.get("refresh_token")
        refresh_token = str(refresh_token_raw) if refresh_token_raw is not None else None
        expires_at = _compute_expires_at(token_response.get("expires_in"))

        if server.credential_scope == "org":
            await self._persist_org(
                server=server,
                actor_user_id=actor_user_id,
                client_config=client_config,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at_iso=utc_isoformat(expires_at),
            )
        elif server.credential_scope == "user":
            await self._persist_user(
                server=server,
                actor_user_id=actor_user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
        else:
            raise OAuthInvalidServerState(
                f"server {server.id} has unsupported credential_scope="
                f"{server.credential_scope!r} for OAuth"
            )

        # Deferred import to break the cubebox.mcp.cubepi_admin_refresh ↔
        # oauth.callback circular import (the refresh module pulls in
        # repositories/models that may import OAuthTokenManager via this package).
        from cubebox.mcp.cubepi_admin_refresh import refresh_tools_for_server_with_token

        await refresh_tools_for_server_with_token(
            server,
            server_repo=self._server_repo,
            credential_or_token=access_token,
        )

    async def _persist_org(
        self,
        *,
        server: MCPServer,
        actor_user_id: str,
        client_config: dict[str, Any],
        access_token: str,
        refresh_token: str | None,
        expires_at_iso: str,
    ) -> None:
        cred_service = self._cred_service_factory(server.org_id, actor_user_id)
        # Upsert: re-OAuth (admin Re-authenticate, refresh_token expiry) hits
        # this path with the same (kind, name) tuple, and the unique index
        # ``uq_credential_org_kind_name`` would 500 the callback if we always
        # created a new row.
        access_id = await cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name=f"mcp:{server.name}:org:access",
            plaintext=access_token,
        )
        refresh_id: str | None = None
        if refresh_token is not None:
            refresh_id = await cred_service.upsert_by_kind_name(
                kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
                name=f"mcp:{server.name}:org:refresh",
                plaintext=refresh_token,
            )

        server.credential_id = access_id
        client_config["expires_at"] = expires_at_iso
        if refresh_id is not None:
            client_config["refresh_token_credential_id"] = refresh_id
        # If refresh_id is None the AS chose not to rotate the refresh token
        # this round. Per RFC 6749 §6 the previously issued refresh token
        # remains valid, so leave the existing pointer alone — clearing it
        # would orphan a still-usable credential row and force the next
        # refresh to raise OAuthInvalidServerState.
        server.oauth_client_config = client_config
        server.authed = True
        server.last_error = None
        await self._server_repo.update(server)

    async def _persist_user(
        self,
        *,
        server: MCPServer,
        actor_user_id: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: Any,
    ) -> None:
        cred_service = self._cred_service_factory(server.org_id, actor_user_id)
        # Upsert mirrors the org path — see _persist_org for the rationale.
        access_id = await cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name=f"mcp:{server.name}:user:{actor_user_id}:access",
            plaintext=access_token,
        )
        refresh_id: str | None = None
        if refresh_token is not None:
            refresh_id = await cred_service.upsert_by_kind_name(
                kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
                name=f"mcp:{server.name}:user:{actor_user_id}:refresh",
                plaintext=refresh_token,
            )

        existing = await self._user_cred_repo.get(
            user_id=actor_user_id,
            mcp_server_id=server.id,
        )
        if existing is None:
            await self._user_cred_repo.add(
                UserMCPCredential(
                    org_id=server.org_id,
                    user_id=actor_user_id,
                    mcp_server_id=server.id,
                    credential_id=access_id,
                    oauth_refresh_token_credential_id=refresh_id,
                    oauth_expires_at=expires_at,
                )
            )
        else:
            existing.credential_id = access_id
            # Same RFC 6749 §6 invariant as _persist_org: only overwrite the
            # refresh pointer when the AS actually issued a new refresh token
            # this round. ``refresh_id is None`` means rotation was skipped,
            # not that the prior refresh token has been invalidated.
            if refresh_id is not None:
                existing.oauth_refresh_token_credential_id = refresh_id
            existing.oauth_expires_at = expires_at
            await self._user_cred_repo.session.commit()
            await self._user_cred_repo.session.refresh(existing)

        server.authed = True
        server.last_error = None
        await self._server_repo.update(server)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
