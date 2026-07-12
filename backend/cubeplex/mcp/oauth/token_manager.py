"""OAuth access-token lifecycle manager (four-layer grants).

Returns a valid access token for a given ``MCPCredentialGrant`` row.
When the cached token is within the refresh buffer of expiry, the manager
performs an RFC 6749 refresh grant under a redis lock so concurrent
runtime requests collapse to a single token endpoint hit. New refresh
tokens (RFC 6749 §6 rotation) replace the previous one in the vault in
place.

Failure modes:

- ``OAuthRefreshFailed`` — terminal: AS rejected the grant (401 /
  invalid_grant). The grant row's ``grant_status`` is flipped to
  ``"expired"`` so ``compute_effective_state`` surfaces ``grant_expired``
  and the connector is dropped from the runtime.
- ``OAuthRefreshContention`` — transient: another worker is mid-refresh
  and didn't finish within the lock window. Caller should retry shortly.
- ``OAuthInvalidServerState`` — programmer error: the grant row is in an
  inconsistent state (missing client_id / refresh_credential_id, etc.).

This module is pure async + injectable. The redis lock TTL is intentionally
short (default 5s) so a crashed worker can't park the lock; the polling
waiter sleeps in 100ms increments up to the same window before giving up.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from redis.asyncio import Redis

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
)
from cubeplex.mcp.exceptions import (
    OAuthInvalidServerState,
    OAuthRefreshContention,
    OAuthRefreshFailed,
)
from cubeplex.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubeplex.models import MCPCredentialGrant
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.mcp import MCPCredentialGrantRepository

REFRESH_LOCK_KEY_PREFIX = "mcp_oauth_refresh:"


class OAuthTokenManager:
    """Reads + refreshes OAuth tokens for an MCPCredentialGrant row."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        redis: Redis,
        encryption_backend: EncryptionBackend,
        credential_repo: CredentialRepository,
        metadata: OAuthMetadataDiscovery,
        refresh_buffer_seconds: int = 60,
        lock_ttl_seconds: int = 5,
    ) -> None:
        self._http = http_client
        self._redis = redis
        self._backend = encryption_backend
        self._credential_repo = credential_repo
        self._metadata = metadata
        self._refresh_buffer = refresh_buffer_seconds
        self._lock_ttl = lock_ttl_seconds

    async def get_access_token_for_grant(
        self,
        *,
        grant: MCPCredentialGrant,
        grant_repo: MCPCredentialGrantRepository,
        server_url: str,
        oauth_client_config: dict[str, Any],
    ) -> str:
        """Return a valid access token for a four-layer OAuth grant.

        Caller contract:

        * ``grant.credential_id`` references the current access-token row in
          the vault.
        * ``grant.refresh_credential_id`` references the refresh-token row
          (required to perform a refresh; absent → terminal failure).
        * ``grant.expires_at`` is the cached access-token expiry. Refresh
          fires when this is within ``refresh_buffer_seconds`` of now.
        * ``oauth_client_config`` carries ``client_id`` (and optionally
          ``client_secret_credential_id``) on the install row.

        Side effects on success: both vault rows are updated in place; the
        grant row's ``expires_at`` advances and ``grant_status`` is forced
        back to ``"valid"`` (e.g. clearing a stale expired marker). On
        :class:`OAuthRefreshFailed` the grant row's ``grant_status`` is
        flipped to ``"expired"`` so the next ``compute_effective_state``
        call surfaces ``grant_expired`` and the runtime drops the
        connector.
        """
        if grant.refresh_credential_id is None:
            raise OAuthInvalidServerState(
                f"grant {grant.id} has no refresh_credential_id; cannot refresh"
            )

        if not self._needs_refresh(grant.expires_at):
            return await self._read_credential(grant.credential_id)

        # Lock on the access-token credential id — the row we'll rotate.
        return await self._refresh_with_lock(
            lock_key=REFRESH_LOCK_KEY_PREFIX + grant.credential_id,
            do_refresh=lambda: self._refresh_grant(
                grant=grant,
                grant_repo=grant_repo,
                server_url=server_url,
                oauth_client_config=oauth_client_config,
            ),
            re_read=lambda: self._read_grant_after_lock(
                grant=grant,
                grant_repo=grant_repo,
            ),
        )

    async def _read_grant_after_lock(
        self,
        *,
        grant: MCPCredentialGrant,
        grant_repo: MCPCredentialGrantRepository,
    ) -> str:
        """Re-read the grant after lock contention to see another worker's result."""
        fresh = await self._reread_grant(grant=grant, grant_repo=grant_repo)
        if fresh is None:
            raise OAuthInvalidServerState(f"grant {grant.id} disappeared during refresh")
        if self._needs_refresh(fresh.expires_at):
            raise OAuthRefreshContention(
                f"refresh on grant {grant.id} did not complete within lock window"
            )
        return await self._read_credential(fresh.credential_id)

    async def _refresh_grant(
        self,
        *,
        grant: MCPCredentialGrant,
        grant_repo: MCPCredentialGrantRepository,
        server_url: str,
        oauth_client_config: dict[str, Any],
    ) -> str:
        """Perform the refresh inside the lock; persist credentials + grant."""
        # Re-read inside the lock so we don't double-refresh if another worker
        # just won.
        fresh = await self._reread_grant(grant=grant, grant_repo=grant_repo)
        if fresh is None:
            raise OAuthInvalidServerState(f"grant {grant.id} disappeared during refresh")
        if fresh.refresh_credential_id is None:
            raise OAuthInvalidServerState(
                f"grant {fresh.id} has no refresh_credential_id; cannot refresh"
            )
        if not self._needs_refresh(fresh.expires_at):
            return await self._read_credential(fresh.credential_id)

        refresh_token = await self._read_credential(fresh.refresh_credential_id)

        try:
            token_response = await self._post_refresh_grant_endpoint(
                server_url=server_url,
                client_config=oauth_client_config,
                refresh_token=refresh_token,
                state_context=f"grant {fresh.id}",
            )
        except OAuthRefreshFailed:
            # Mark the grant expired so compute_effective_state surfaces
            # ``grant_expired`` and the connector is dropped from the runtime.
            # Vault rows are kept intact — the refresh credential may still be
            # required for a manual reauthorize flow.
            with suppress(Exception):
                fresh.grant_status = "expired"
                await grant_repo.update(fresh)
            raise

        new_access = str(token_response["access_token"])
        new_refresh = str(token_response.get("refresh_token", refresh_token))
        new_expires_at = _compute_expires_at(token_response.get("expires_in"))

        await self._update_credential(fresh.credential_id, new_access)
        await self._update_credential(fresh.refresh_credential_id, new_refresh)

        fresh.expires_at = new_expires_at
        fresh.grant_status = "valid"
        await grant_repo.update(fresh)
        return new_access

    async def _reread_grant(
        self,
        *,
        grant: MCPCredentialGrant,
        grant_repo: MCPCredentialGrantRepository,
    ) -> MCPCredentialGrant | None:
        """Re-fetch ``grant`` from the right scope-keyed lookup."""
        if grant.grant_scope == "org":
            return await grant_repo.get_org_grant_for_connector(grant.connector_id)
        if grant.grant_scope == "workspace":
            if grant.workspace_id is None:
                return None
            return await grant_repo.get_workspace_grant_for_connector(
                grant.connector_id,
                grant.workspace_id,
            )
        if grant.grant_scope == "user":
            if grant.user_id is None or grant.workspace_id is None:
                return None
            return await grant_repo.get_user_grant_for_connector(
                grant.connector_id,
                grant.user_id,
                workspace_id=grant.workspace_id,
            )
        return None

    # ---------------- shared mechanics ---------------- #

    async def _refresh_with_lock(
        self,
        *,
        lock_key: str,
        do_refresh: Callable[[], Awaitable[str]],
        re_read: Callable[[], Awaitable[str]],
    ) -> str:
        """Acquire a redis lock, run ``do_refresh``; on contention call ``re_read``."""
        acquired = await self._redis.set(lock_key, "1", nx=True, ex=self._lock_ttl)
        if not acquired:
            return await self._wait_then_reread(lock_key=lock_key, re_read=re_read)
        try:
            return await do_refresh()
        finally:
            await self._redis.delete(lock_key)

    async def _wait_then_reread(
        self, *, lock_key: str, re_read: Callable[[], Awaitable[str]]
    ) -> str:
        deadline = self._lock_ttl + 1
        elapsed = 0.0
        while elapsed < deadline:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            still_held = await self._redis.exists(lock_key)
            if not still_held:
                return await re_read()
        return await re_read()

    async def _post_refresh_grant_endpoint(
        self,
        *,
        server_url: str,
        client_config: dict[str, Any],
        refresh_token: str,
        state_context: str,
    ) -> dict[str, Any]:
        """Pure wire-level refresh exchange — no DB side effects on failure.

        Resolves the authorization-server metadata for ``server_url``, POSTs
        ``grant_type=refresh_token``, and returns the parsed JSON body on
        success. Raises :class:`OAuthInvalidServerState` if the client
        configuration is incomplete; raises :class:`OAuthRefreshFailed` on
        non-2xx or a malformed 200 body. Callers are responsible for any
        scope-specific fallout (flipping grant status, etc.).
        """
        client_id = client_config.get("client_id")
        if not isinstance(client_id, str):
            raise OAuthInvalidServerState(f"{state_context} oauth_client_config has no client_id")

        # Locate AS. Discovery is cached; ``server_url`` is the protected resource.
        _, as_meta = await self._metadata.discover_for_resource(server_url)

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        client_secret_credential_id = client_config.get("client_secret_credential_id")
        if isinstance(client_secret_credential_id, str):
            client_secret = await self._read_credential(client_secret_credential_id)
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"

        response = await self._http.post(
            as_meta.token_endpoint,
            data=data,
            headers=headers,
        )

        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict) or "access_token" not in body:
                raise OAuthRefreshFailed(
                    response.status_code,
                    error="invalid_response",
                    error_description="token response missing access_token",
                )
            return cast(dict[str, Any], body)

        # Non-200 → terminal. Caller is responsible for scope-specific cleanup.
        error: str | None = None
        error_description: str | None = None
        try:
            err_body = response.json()
            if isinstance(err_body, dict):
                error = _opt_str(err_body.get("error"))
                error_description = _opt_str(err_body.get("error_description"))
        except ValueError:
            pass
        raise OAuthRefreshFailed(
            response.status_code,
            error=error,
            error_description=error_description,
        )

    async def _read_credential(self, credential_id: str) -> str:
        cred = await self._credential_repo.get(credential_id)
        if cred is None:
            raise OAuthInvalidServerState(f"credential {credential_id} not found")
        plaintext = await self._backend.decrypt(cred.value_encrypted)
        return plaintext.decode("utf-8")

    async def _update_credential(self, credential_id: str, plaintext: str) -> None:
        cred = await self._credential_repo.get(credential_id)
        if cred is None:
            raise OAuthInvalidServerState(f"credential {credential_id} not found")
        cred.value_encrypted = await self._backend.encrypt(plaintext.encode("utf-8"))
        await self._credential_repo.update(cred)

    def _needs_refresh(self, expires_at: datetime | None) -> bool:
        if expires_at is None:
            return True
        if expires_at.tzinfo is None:  # SQLite discards tz on round-trip
            expires_at = expires_at.replace(tzinfo=UTC)
        delta = (expires_at - datetime.now(UTC)).total_seconds()
        return delta <= self._refresh_buffer


def _compute_expires_at(expires_in: Any) -> datetime:
    """Convert RFC 6749 ``expires_in`` (seconds) → absolute UTC datetime."""
    seconds: int
    if isinstance(expires_in, int):
        seconds = expires_in
    else:
        try:
            seconds = int(str(expires_in))
        except (TypeError, ValueError):
            seconds = 3600  # one hour fallback when AS omits expires_in
    return datetime.now(UTC) + timedelta(seconds=max(seconds, 0))


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


# Re-export the constants for callers that need to construct vault rows.
__all__ = [
    "CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN",
    "CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN",
    "OAuthTokenManager",
    "REFRESH_LOCK_KEY_PREFIX",
]
