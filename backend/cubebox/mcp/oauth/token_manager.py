"""OAuth access-token lifecycle manager.

Returns a valid access token for a given ``MCPServer`` install (org-scope) or
``(server, user_id)`` pair (user-scope). When the cached token is within the
refresh buffer of expiry, the manager performs an RFC 6749 refresh grant under
a redis lock so concurrent runtime requests collapse to a single token endpoint
hit. New refresh tokens (RFC 6749 §6 rotation) replace the previous one in the
vault in place.

Failure modes:

- ``OAuthRefreshFailed`` — terminal: AS rejected the grant (401 / invalid_grant).
  Server is marked ``authed=False`` (org) or the user_cred row is cleared
  (user). UI is expected to surface "Reauthorize required".
- ``OAuthRefreshContention`` — transient: another worker is mid-refresh and
  didn't finish within the lock window. Caller should retry shortly.
- ``OAuthInvalidServerState`` — programmer error: the caller asked for a token
  on a server whose ``auth_method`` is not ``"oauth"``.

This module is pure async + injectable. The redis lock TTL is intentionally
short (default 5s) so a crashed worker can't park the lock; the polling waiter
sleeps in 100ms increments up to the same window before giving up.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from redis.asyncio import Redis

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
)
from cubebox.mcp.exceptions import (
    OAuthInvalidServerState,
    OAuthRefreshContention,
    OAuthRefreshFailed,
)
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.models import MCPServer, UserMCPCredential
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository, UserMCPCredentialRepository
from cubebox.utils.time import utc_isoformat

REFRESH_LOCK_KEY_PREFIX = "mcp_oauth_refresh:"


class OAuthTokenManager:
    """Reads + refreshes OAuth tokens for an MCPServer install."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        redis: Redis,
        encryption_backend: EncryptionBackend,
        credential_repo: CredentialRepository,
        server_repo: MCPServerRepository,
        user_cred_repo: UserMCPCredentialRepository,
        metadata: OAuthMetadataDiscovery,
        refresh_buffer_seconds: int = 60,
        lock_ttl_seconds: int = 5,
    ) -> None:
        self._http = http_client
        self._redis = redis
        self._backend = encryption_backend
        self._credential_repo = credential_repo
        self._server_repo = server_repo
        self._user_cred_repo = user_cred_repo
        self._metadata = metadata
        self._refresh_buffer = refresh_buffer_seconds
        self._lock_ttl = lock_ttl_seconds

    async def get_valid_access_token(
        self,
        server: MCPServer,
        *,
        user_id: str | None = None,
    ) -> str:
        """Return a valid access token, refreshing it if near expiry."""
        if server.auth_method != "oauth":
            raise OAuthInvalidServerState(
                f"server {server.id} has auth_method={server.auth_method!r}, not 'oauth'"
            )

        scope = server.credential_scope
        if scope == "user":
            if user_id is None:
                raise OAuthInvalidServerState(
                    f"server {server.id} has credential_scope=user but no user_id was supplied"
                )
            return await self._get_user_token(server=server, user_id=user_id)
        if scope == "org":
            return await self._get_org_token(server=server)
        raise OAuthInvalidServerState(
            f"server {server.id} has unsupported credential_scope={scope!r} for OAuth"
        )

    # ---------------- org scope ---------------- #

    async def _get_org_token(self, *, server: MCPServer) -> str:
        access_credential_id = server.credential_id
        if access_credential_id is None:
            raise OAuthInvalidServerState(
                f"server {server.id} has no access_token credential — install incomplete"
            )
        expires_at = _read_iso(server.oauth_client_config.get("expires_at"))
        if not self._needs_refresh(expires_at):
            return await self._read_credential(access_credential_id)

        # Lock on the access-token credential id — that's the row we'll rotate.
        return await self._refresh_with_lock(
            lock_key=REFRESH_LOCK_KEY_PREFIX + access_credential_id,
            do_refresh=lambda: self._refresh_org(server),
            re_read=lambda: self._read_org_after_lock(server),
        )

    async def _read_org_after_lock(self, server: MCPServer) -> str:
        """Re-read DB inside the contention waiter to see another worker's result."""
        fresh = await self._server_repo.get(server.id)
        if fresh is None or fresh.credential_id is None:
            raise OAuthInvalidServerState(f"server {server.id} disappeared during refresh")
        expires_at = _read_iso(fresh.oauth_client_config.get("expires_at"))
        if self._needs_refresh(expires_at):
            raise OAuthRefreshContention(
                f"refresh on {server.id} did not complete within lock window"
            )
        return await self._read_credential(fresh.credential_id)

    async def _refresh_org(self, server: MCPServer) -> str:
        # Re-read inside the lock so we don't double-refresh if another worker
        # just won.
        fresh = await self._server_repo.get(server.id)
        if fresh is None or fresh.credential_id is None:
            raise OAuthInvalidServerState(f"server {server.id} disappeared during refresh")
        expires_at = _read_iso(fresh.oauth_client_config.get("expires_at"))
        if not self._needs_refresh(expires_at):
            return await self._read_credential(fresh.credential_id)

        client_config = dict(fresh.oauth_client_config or {})
        refresh_credential_id = client_config.get("refresh_token_credential_id")
        if not isinstance(refresh_credential_id, str):
            raise OAuthInvalidServerState(
                f"server {fresh.id} has no refresh_token_credential_id in oauth_client_config"
            )
        refresh_token = await self._read_credential(refresh_credential_id)

        token_response = await self._exchange_refresh_token(
            server=fresh,
            client_config=client_config,
            refresh_token=refresh_token,
        )

        new_access = str(token_response["access_token"])
        new_refresh = str(token_response.get("refresh_token", refresh_token))
        new_expires_at = _compute_expires_at(token_response.get("expires_in"))

        await self._update_credential(fresh.credential_id, new_access)
        await self._update_credential(refresh_credential_id, new_refresh)

        client_config["expires_at"] = utc_isoformat(new_expires_at)
        fresh.oauth_client_config = client_config
        fresh.authed = True
        fresh.last_error = None
        await self._server_repo.update(fresh)
        return new_access

    # ---------------- user scope ---------------- #

    async def _get_user_token(self, *, server: MCPServer, user_id: str) -> str:
        user_cred = await self._user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server.id,
        )
        if user_cred is None or user_cred.oauth_refresh_token_credential_id is None:
            raise OAuthInvalidServerState(
                f"user {user_id} has no OAuth credential for server {server.id}"
            )
        if not self._needs_refresh(user_cred.oauth_expires_at):
            return await self._read_credential(user_cred.credential_id)

        return await self._refresh_with_lock(
            lock_key=REFRESH_LOCK_KEY_PREFIX + user_cred.credential_id,
            do_refresh=lambda: self._refresh_user(server=server, user_id=user_id),
            re_read=lambda: self._read_user_after_lock(server=server, user_id=user_id),
        )

    async def _read_user_after_lock(self, *, server: MCPServer, user_id: str) -> str:
        fresh = await self._user_cred_repo.get(user_id=user_id, mcp_server_id=server.id)
        if fresh is None:
            raise OAuthInvalidServerState(
                f"user_mcp_credential disappeared for user={user_id} server={server.id}"
            )
        if self._needs_refresh(fresh.oauth_expires_at):
            raise OAuthRefreshContention(
                f"refresh on (user={user_id}, server={server.id}) did not complete in lock window"
            )
        return await self._read_credential(fresh.credential_id)

    async def _refresh_user(self, *, server: MCPServer, user_id: str) -> str:
        fresh_server = await self._server_repo.get(server.id)
        if fresh_server is None:
            raise OAuthInvalidServerState(f"server {server.id} disappeared during refresh")
        user_cred = await self._user_cred_repo.get(user_id=user_id, mcp_server_id=server.id)
        if user_cred is None or user_cred.oauth_refresh_token_credential_id is None:
            raise OAuthInvalidServerState(
                f"user_mcp_credential row gone for user={user_id} server={server.id}"
            )
        if not self._needs_refresh(user_cred.oauth_expires_at):
            return await self._read_credential(user_cred.credential_id)

        client_config = dict(fresh_server.oauth_client_config or {})
        refresh_token = await self._read_credential(user_cred.oauth_refresh_token_credential_id)

        try:
            token_response = await self._exchange_refresh_token(
                server=fresh_server,
                client_config=client_config,
                refresh_token=refresh_token,
            )
        except OAuthRefreshFailed:
            await self._purge_user_credentials(user_cred)
            raise

        new_access = str(token_response["access_token"])
        new_refresh = str(token_response.get("refresh_token", refresh_token))
        new_expires_at = _compute_expires_at(token_response.get("expires_in"))

        await self._update_credential(user_cred.credential_id, new_access)
        await self._update_credential(
            user_cred.oauth_refresh_token_credential_id,
            new_refresh,
        )
        user_cred.oauth_expires_at = new_expires_at
        await self._user_cred_repo.session.commit()
        await self._user_cred_repo.session.refresh(user_cred)
        return new_access

    async def _purge_user_credentials(self, user_cred: UserMCPCredential) -> None:
        for cred_id in (
            user_cred.credential_id,
            user_cred.oauth_refresh_token_credential_id,
        ):
            if cred_id is None:
                continue
            with suppress(CredentialNotFound):
                await self._credential_repo.delete(cred_id)
        await self._user_cred_repo.delete(
            user_id=user_cred.user_id,
            mcp_server_id=user_cred.mcp_server_id,
        )

    # ---------------- shared mechanics ---------------- #

    async def _refresh_with_lock(
        self,
        *,
        lock_key: str,
        do_refresh: Any,
        re_read: Any,
    ) -> str:
        """Acquire a redis lock, run ``do_refresh``; on contention call ``re_read``."""
        acquired = await self._redis.set(lock_key, "1", nx=True, ex=self._lock_ttl)
        if not acquired:
            return await self._wait_then_reread(lock_key=lock_key, re_read=re_read)
        try:
            return cast(str, await do_refresh())
        finally:
            await self._redis.delete(lock_key)

    async def _wait_then_reread(self, *, lock_key: str, re_read: Any) -> str:
        deadline = self._lock_ttl + 1
        elapsed = 0.0
        while elapsed < deadline:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            still_held = await self._redis.exists(lock_key)
            if not still_held:
                return cast(str, await re_read())
        return cast(str, await re_read())

    async def _exchange_refresh_token(
        self,
        *,
        server: MCPServer,
        client_config: dict[str, Any],
        refresh_token: str,
    ) -> dict[str, Any]:
        """POST grant_type=refresh_token, return parsed JSON or raise."""
        client_id = client_config.get("client_id")
        if not isinstance(client_id, str):
            raise OAuthInvalidServerState(
                f"server {server.id} oauth_client_config has no client_id"
            )

        # Locate AS. Discovery is cached; ``server_url`` is the protected resource.
        _, as_meta = await self._metadata.discover_for_resource(server.server_url)

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
                # Malformed success — treat as terminal so we don't loop.
                await self._mark_server_unauthed(server, "oauth refresh: malformed token response")
                raise OAuthRefreshFailed(
                    response.status_code,
                    error="invalid_response",
                    error_description="token response missing access_token",
                )
            return cast(dict[str, Any], body)

        # Non-200 → terminal. Mark server unauthed (org path); user path is
        # handled by the caller.
        error: str | None = None
        error_description: str | None = None
        try:
            err_body = response.json()
            if isinstance(err_body, dict):
                error = _opt_str(err_body.get("error"))
                error_description = _opt_str(err_body.get("error_description"))
        except ValueError:
            pass
        if server.credential_scope == "org":
            await self._mark_server_unauthed(
                server,
                f"oauth refresh failed: {error or response.status_code}",
            )
            await self._purge_org_credentials(server)
        raise OAuthRefreshFailed(
            response.status_code,
            error=error,
            error_description=error_description,
        )

    async def _mark_server_unauthed(self, server: MCPServer, reason: str) -> None:
        fresh = await self._server_repo.get(server.id)
        if fresh is None:
            return
        fresh.authed = False
        fresh.last_error = reason[:2048]
        await self._server_repo.update(fresh)

    async def _purge_org_credentials(self, server: MCPServer) -> None:
        fresh = await self._server_repo.get(server.id)
        if fresh is None:
            return
        access_id = fresh.credential_id
        refresh_id = (fresh.oauth_client_config or {}).get("refresh_token_credential_id")
        for cred_id in (access_id, refresh_id):
            if isinstance(cred_id, str):
                with suppress(CredentialNotFound):
                    await self._credential_repo.delete(cred_id)
        fresh.credential_id = None
        client_config = dict(fresh.oauth_client_config or {})
        client_config.pop("refresh_token_credential_id", None)
        client_config.pop("expires_at", None)
        fresh.oauth_client_config = client_config
        await self._server_repo.update(fresh)

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
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        delta = (expires_at - datetime.now(UTC)).total_seconds()
        return delta <= self._refresh_buffer


def _read_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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
