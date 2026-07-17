"""Unit tests for ``OAuthTokenManager`` forced refresh.

Providers may revoke access tokens before the ``expires_in`` they
reported (Cloudflare did, 2026-07-17). The contract under test: a
caller that saw a live 401 can demand a refresh regardless of the
recorded expiry (``force_refresh=True``), concurrent forced refreshes
collapse to a single rotation, and failure semantics (grant flipped to
``expired``) survive the forced path.

Everything external is faked in-process: redis (lock ops only), the
credential vault repo, the encryption backend, the grant repo, and the
AS token endpoint (httpx.MockTransport).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from cubeplex.mcp.exceptions import OAuthRefreshFailed
from cubeplex.mcp.oauth.token_manager import OAuthTokenManager
from cubeplex.models import MCPCredentialGrant

_TOKEN_ENDPOINT = "https://auth.example.com/token"


class _FakeRedis:
    """Just enough of redis.asyncio for the refresh lock."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int = 0) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> int:
        return int(key in self._store)


class _IdentityBackend:
    async def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext

    async def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext


@dataclass
class _Cred:
    id: str
    value_encrypted: bytes


class _FakeCredentialRepo:
    def __init__(self, creds: dict[str, bytes]) -> None:
        self._rows = {cid: _Cred(cid, val) for cid, val in creds.items()}

    async def get(self, credential_id: str) -> _Cred | None:
        return self._rows.get(credential_id)

    async def update(self, cred: _Cred) -> _Cred:
        self._rows[cred.id] = cred
        return cred


@dataclass
class _FakeGrantRepo:
    """Scope-keyed lookups return ``current``; ``update`` records writes."""

    current: MCPCredentialGrant
    updated: list[MCPCredentialGrant] = field(default_factory=list)

    async def get_org_grant_for_connector(self, connector_id: str) -> MCPCredentialGrant:
        return self.current

    async def get_workspace_grant_for_connector(
        self, connector_id: str, workspace_id: str
    ) -> MCPCredentialGrant:
        return self.current

    async def get_user_grant_for_connector(
        self, connector_id: str, user_id: str, *, workspace_id: str
    ) -> MCPCredentialGrant:
        return self.current

    async def update(self, grant: MCPCredentialGrant) -> MCPCredentialGrant:
        self.updated.append(grant)
        return grant


class _FakeMetadata:
    async def discover_for_resource(self, server_url: str) -> tuple[Any, Any]:
        from types import SimpleNamespace

        return None, SimpleNamespace(token_endpoint=_TOKEN_ENDPOINT)


def _make_grant(*, expires_at: datetime | None) -> MCPCredentialGrant:
    return MCPCredentialGrant(
        org_id="org_1",
        connector_id="mcpco_1",
        grant_scope="workspace",
        auth_method="oauth",
        workspace_id="ws_1",
        credential_id="cred_access",
        refresh_credential_id="cred_refresh",
        expires_at=expires_at,
        grant_status="valid",
    )


def _make_manager(
    *,
    grant_repo_grant: MCPCredentialGrant,
    token_status: int = 200,
    calls: list[httpx.Request] | None = None,
) -> tuple[OAuthTokenManager, _FakeGrantRepo, _FakeCredentialRepo]:
    recorded = calls if calls is not None else []

    def _handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if token_status != 200:
            return httpx.Response(token_status, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )

    cred_repo = _FakeCredentialRepo({"cred_access": b"old-access", "cred_refresh": b"old-refresh"})
    grant_repo = _FakeGrantRepo(current=grant_repo_grant)
    manager = OAuthTokenManager(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        redis=_FakeRedis(),  # type: ignore[arg-type]
        encryption_backend=_IdentityBackend(),  # type: ignore[arg-type]
        credential_repo=cred_repo,  # type: ignore[arg-type]
        metadata=_FakeMetadata(),  # type: ignore[arg-type]
    )
    return manager, grant_repo, cred_repo


_CLIENT_CONFIG = {"client_id": "client-1"}


@pytest.mark.asyncio
async def test_force_refresh_rotates_despite_future_expiry() -> None:
    """A revoked-but-unexpired token must be replaceable on demand —
    the time-based check alone left dead tokens in play for hours."""
    grant = _make_grant(expires_at=datetime.now(UTC) + timedelta(hours=10))
    calls: list[httpx.Request] = []
    manager, _grant_repo, cred_repo = _make_manager(grant_repo_grant=grant, calls=calls)

    token = await manager.get_access_token_for_grant(
        grant=grant,
        grant_repo=_grant_repo,  # type: ignore[arg-type]
        server_url="https://mcp.example.com/mcp",
        oauth_client_config=_CLIENT_CONFIG,
        force_refresh=True,
    )

    assert token == "new-access"
    assert len(calls) == 1
    assert grant.grant_status == "valid"
    assert grant.expires_at is not None
    assert grant.expires_at - datetime.now(UTC) < timedelta(hours=2)
    stored_access = await cred_repo.get("cred_access")
    stored_refresh = await cred_repo.get("cred_refresh")
    assert stored_access is not None and stored_access.value_encrypted == b"new-access"
    assert stored_refresh is not None and stored_refresh.value_encrypted == b"new-refresh"


@pytest.mark.asyncio
async def test_force_refresh_skips_rotation_when_another_worker_won() -> None:
    """Two workers racing on the same 401 must not double-rotate: the
    second one sees the advanced expires_at and reuses the new token."""
    observed = _make_grant(expires_at=datetime.now(UTC) - timedelta(minutes=5))
    already_rotated = _make_grant(expires_at=datetime.now(UTC) + timedelta(hours=1))
    calls: list[httpx.Request] = []
    manager, _grant_repo, _cred_repo = _make_manager(grant_repo_grant=already_rotated, calls=calls)

    token = await manager.get_access_token_for_grant(
        grant=observed,
        grant_repo=_grant_repo,  # type: ignore[arg-type]
        server_url="https://mcp.example.com/mcp",
        oauth_client_config=_CLIENT_CONFIG,
        force_refresh=True,
    )

    # The other worker's rotation already wrote the vault row; no second
    # token-endpoint hit.
    assert token == "old-access"
    assert calls == []


@pytest.mark.asyncio
async def test_force_refresh_failure_marks_grant_expired() -> None:
    grant = _make_grant(expires_at=datetime.now(UTC) + timedelta(hours=10))
    manager, grant_repo, _cred_repo = _make_manager(grant_repo_grant=grant, token_status=400)

    with pytest.raises(OAuthRefreshFailed):
        await manager.get_access_token_for_grant(
            grant=grant,
            grant_repo=grant_repo,  # type: ignore[arg-type]
            server_url="https://mcp.example.com/mcp",
            oauth_client_config=_CLIENT_CONFIG,
            force_refresh=True,
        )

    assert grant.grant_status == "expired"
    assert grant_repo.updated, "expired status must be persisted"


@pytest.mark.asyncio
async def test_default_path_still_time_based() -> None:
    grant = _make_grant(expires_at=datetime.now(UTC) + timedelta(hours=10))
    calls: list[httpx.Request] = []
    manager, grant_repo, _cred_repo = _make_manager(grant_repo_grant=grant, calls=calls)

    token = await manager.get_access_token_for_grant(
        grant=grant,
        grant_repo=grant_repo,  # type: ignore[arg-type]
        server_url="https://mcp.example.com/mcp",
        oauth_client_config=_CLIENT_CONFIG,
    )

    assert token == "old-access"
    assert calls == []


@pytest.mark.asyncio
async def test_with_credential_repo_clone_reads_from_new_repo() -> None:
    """The runtime retry path rebinds the manager to a fresh session's
    credential repo; the clone must read/write through the new repo."""
    grant = _make_grant(expires_at=datetime.now(UTC) + timedelta(hours=10))
    manager, grant_repo, _old_repo = _make_manager(grant_repo_grant=grant)

    new_repo = _FakeCredentialRepo(
        {"cred_access": b"other-access", "cred_refresh": b"other-refresh"}
    )
    clone = manager.with_credential_repo(new_repo)  # type: ignore[arg-type]

    token = await clone.get_access_token_for_grant(
        grant=grant,
        grant_repo=grant_repo,  # type: ignore[arg-type]
        server_url="https://mcp.example.com/mcp",
        oauth_client_config=_CLIENT_CONFIG,
    )
    assert token == "other-access"
