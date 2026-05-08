"""Unit tests for cubebox.mcp.oauth.token_manager."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
    server_url_hash,
)
from cubebox.mcp.exceptions import (
    OAuthInvalidServerState,
    OAuthRefreshFailed,
)
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.models import Credential, MCPServer, UserMCPCredential
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository, UserMCPCredentialRepository
from cubebox.utils.time import utc_isoformat

ORG_ID = "org-test"
USER_ID = "user-test"
SERVER_URL = "https://mcp.example.com"
TOKEN_ENDPOINT = "https://auth.example.com/oauth/token"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield fake
    finally:
        await fake.flushall()


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


def _well_known_url(base: str) -> str:
    return f"{base.rstrip('/')}/.well-known/oauth-protected-resource"


def _as_well_known_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"


def _metadata_handler() -> dict[str, httpx.Response]:
    return {
        _well_known_url(SERVER_URL): httpx.Response(
            200,
            json={
                "resource": SERVER_URL,
                "authorization_servers": ["https://auth.example.com"],
            },
        ),
        _as_well_known_url("https://auth.example.com"): httpx.Response(
            200,
            json={
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/oauth/authorize",
                "token_endpoint": TOKEN_ENDPOINT,
            },
        ),
    }


class _ScriptedHandler:
    """MockTransport handler with a baseline lookup map + scripted token-endpoint sequence."""

    def __init__(
        self,
        baseline: dict[str, httpx.Response],
        token_responses: list[httpx.Response],
    ) -> None:
        self._baseline = baseline
        self._token_responses = list(token_responses)
        self.token_calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_ENDPOINT:
            self.token_calls.append(
                {
                    "method": request.method,
                    "headers": dict(request.headers),
                    "body": request.read().decode("utf-8"),
                }
            )
            if not self._token_responses:
                return httpx.Response(500, text="no scripted response left")
            return self._token_responses.pop(0)
        if url in self._baseline:
            return self._baseline[url]
        return httpx.Response(404, json={"error": "not_found"})


async def _seed_org_oauth_install(
    session: AsyncSession,
    encryption_backend: FernetBackend,
    *,
    expires_at: datetime,
    access_plaintext: str = "current-access",
    refresh_plaintext: str = "current-refresh",
) -> tuple[MCPServer, Credential, Credential]:
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    access_cred = await cred_repo.add(
        Credential(
            org_id=ORG_ID,
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name="mcp:test:org:access",
            value_encrypted=await encryption_backend.encrypt(access_plaintext.encode("utf-8")),
            cred_metadata={},
        )
    )
    refresh_cred = await cred_repo.add(
        Credential(
            org_id=ORG_ID,
            kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
            name="mcp:test:org:refresh",
            value_encrypted=await encryption_backend.encrypt(refresh_plaintext.encode("utf-8")),
            cred_metadata={},
        )
    )
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    server = await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id=None,
            name="oauth-org-install",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="org",
            credential_id=access_cred.id,
            oauth_client_config={
                "client_id": "client-abc",
                "refresh_token_credential_id": refresh_cred.id,
                "expires_at": utc_isoformat(expires_at),
            },
            authed=True,
            created_by_user_id=USER_ID,
        )
    )
    return server, access_cred, refresh_cred


def _make_manager(
    *,
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    backend: FernetBackend,
    handler: _ScriptedHandler,
    refresh_buffer_seconds: int = 60,
    lock_ttl_seconds: int = 5,
) -> tuple[OAuthTokenManager, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadataDiscovery(http)
    manager = OAuthTokenManager(
        http_client=http,
        redis=fake_redis,
        encryption_backend=backend,
        credential_repo=CredentialRepository(session, org_id=ORG_ID),
        server_repo=MCPServerRepository(session, org_id=ORG_ID),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ORG_ID),
        metadata=metadata,
        refresh_buffer_seconds=refresh_buffer_seconds,
        lock_ttl_seconds=lock_ttl_seconds,
    )
    return manager, http


async def test_get_valid_access_token_no_refresh_when_far_from_expiry(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server, _, _ = await _seed_org_oauth_install(
        session,
        encryption_backend,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        access_plaintext="cached-access",
    )
    handler = _ScriptedHandler(_metadata_handler(), token_responses=[])
    manager, http = _make_manager(
        session=session,
        fake_redis=fake_redis,
        backend=encryption_backend,
        handler=handler,
    )
    try:
        token = await manager.get_valid_access_token(server)
    finally:
        await http.aclose()
    assert token == "cached-access"
    assert handler.token_calls == []  # no AS hit


async def test_get_valid_access_token_refreshes_when_near_expiry(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server, access_cred, refresh_cred = await _seed_org_oauth_install(
        session,
        encryption_backend,
        expires_at=datetime.now(UTC) + timedelta(seconds=10),
        access_plaintext="old-access",
        refresh_plaintext="old-refresh",
    )
    handler = _ScriptedHandler(
        _metadata_handler(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        ],
    )
    manager, http = _make_manager(
        session=session,
        fake_redis=fake_redis,
        backend=encryption_backend,
        handler=handler,
    )
    try:
        token = await manager.get_valid_access_token(server)
    finally:
        await http.aclose()

    assert token == "new-access"
    assert len(handler.token_calls) == 1
    body = handler.token_calls[0]["body"]
    assert "grant_type=refresh_token" in body
    assert "refresh_token=old-refresh" in body
    assert "client_id=client-abc" in body

    # Vault rows updated in place.
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    new_access = await cred_repo.get(access_cred.id)
    new_refresh = await cred_repo.get(refresh_cred.id)
    assert new_access is not None and new_refresh is not None
    assert (await encryption_backend.decrypt(new_access.value_encrypted)).decode() == "new-access"
    assert (await encryption_backend.decrypt(new_refresh.value_encrypted)).decode() == "new-refresh"

    # Server expires_at advanced.
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh_server = await server_repo.get(server.id)
    assert fresh_server is not None
    new_expires = datetime.fromisoformat(fresh_server.oauth_client_config["expires_at"])
    assert new_expires - datetime.now(UTC) > timedelta(minutes=30)


async def test_get_valid_access_token_invalid_grant_marks_unauthed(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server, access_cred, refresh_cred = await _seed_org_oauth_install(
        session,
        encryption_backend,
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    handler = _ScriptedHandler(
        _metadata_handler(),
        token_responses=[
            httpx.Response(
                401,
                json={"error": "invalid_grant", "error_description": "refresh token revoked"},
            )
        ],
    )
    manager, http = _make_manager(
        session=session,
        fake_redis=fake_redis,
        backend=encryption_backend,
        handler=handler,
    )
    try:
        with pytest.raises(OAuthRefreshFailed) as excinfo:
            await manager.get_valid_access_token(server)
    finally:
        await http.aclose()

    assert excinfo.value.status == 401
    assert excinfo.value.error == "invalid_grant"

    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    assert fresh.authed is False
    assert fresh.last_error is not None
    assert "invalid_grant" in fresh.last_error
    assert fresh.credential_id is None
    assert "refresh_token_credential_id" not in fresh.oauth_client_config
    assert "expires_at" not in fresh.oauth_client_config

    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    assert await cred_repo.get(access_cred.id) is None
    assert await cred_repo.get(refresh_cred.id) is None


async def test_get_valid_access_token_concurrent_refresh_dedupes(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """Two concurrent refreshers should collapse to a single AS call."""
    server, _, _ = await _seed_org_oauth_install(
        session,
        encryption_backend,
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
        access_plaintext="initial-access",
        refresh_plaintext="initial-refresh",
    )

    # Custom handler that delays the first response so both coroutines race.
    delay_started = asyncio.Event()
    delay_release = asyncio.Event()

    async def _delayed_first_response() -> httpx.Response:
        delay_started.set()
        await delay_release.wait()
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )

    class _DelayHandler:
        def __init__(self) -> None:
            self.token_calls = 0
            self._baseline = _metadata_handler()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == TOKEN_ENDPOINT:
                self.token_calls += 1
                return await _delayed_first_response()
            if url in self._baseline:
                return self._baseline[url]
            return httpx.Response(404, json={"error": "not_found"})

    handler = _DelayHandler()
    transport = httpx.MockTransport(handler.handle_async_request)
    http = httpx.AsyncClient(transport=transport)
    metadata = OAuthMetadataDiscovery(http)
    manager = OAuthTokenManager(
        http_client=http,
        redis=fake_redis,
        encryption_backend=encryption_backend,
        credential_repo=CredentialRepository(session, org_id=ORG_ID),
        server_repo=MCPServerRepository(session, org_id=ORG_ID),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ORG_ID),
        metadata=metadata,
        refresh_buffer_seconds=60,
        lock_ttl_seconds=5,
    )

    try:
        # Kick off the first refresh; wait until it has the lock + has hit the AS.
        first = asyncio.create_task(manager.get_valid_access_token(server))
        await delay_started.wait()
        # Now start a second refresher; it should observe the lock and wait.
        second = asyncio.create_task(manager.get_valid_access_token(server))
        # Give the second a moment to start polling.
        await asyncio.sleep(0.05)
        delay_release.set()
        token_a = await first
        token_b = await second
    finally:
        await http.aclose()

    assert token_a == "rotated-access"
    assert token_b == "rotated-access"
    # Critical assertion: the AS was hit exactly once even though two
    # coroutines wanted a refresh simultaneously.
    assert handler.token_calls == 1


async def _seed_user_oauth_install(
    session: AsyncSession,
    encryption_backend: FernetBackend,
    *,
    expires_at: datetime,
) -> tuple[MCPServer, UserMCPCredential, Credential, Credential]:
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    access_cred = await cred_repo.add(
        Credential(
            org_id=ORG_ID,
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name="mcp:test:user:access",
            value_encrypted=await encryption_backend.encrypt(b"u-old-access"),
            cred_metadata={},
        )
    )
    refresh_cred = await cred_repo.add(
        Credential(
            org_id=ORG_ID,
            kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
            name="mcp:test:user:refresh",
            value_encrypted=await encryption_backend.encrypt(b"u-old-refresh"),
            cred_metadata={},
        )
    )
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    server = await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id="ws-test",
            name="oauth-user-install",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="user",
            credential_id=None,
            oauth_client_config={"client_id": "client-user"},
            authed=True,
            created_by_user_id=USER_ID,
        )
    )
    user_repo = UserMCPCredentialRepository(session, org_id=ORG_ID)
    user_cred = await user_repo.add(
        UserMCPCredential(
            org_id=ORG_ID,
            user_id=USER_ID,
            mcp_server_id=server.id,
            credential_id=access_cred.id,
            oauth_refresh_token_credential_id=refresh_cred.id,
            oauth_expires_at=expires_at,
        )
    )
    return server, user_cred, access_cred, refresh_cred


async def test_get_valid_access_token_user_scope_refreshes(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server, user_cred, access_cred, refresh_cred = await _seed_user_oauth_install(
        session,
        encryption_backend,
        expires_at=datetime.now(UTC) + timedelta(seconds=5),
    )
    handler = _ScriptedHandler(
        _metadata_handler(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "u-new-access",
                    "refresh_token": "u-new-refresh",
                    "expires_in": 3600,
                },
            )
        ],
    )
    manager, http = _make_manager(
        session=session,
        fake_redis=fake_redis,
        backend=encryption_backend,
        handler=handler,
    )
    try:
        token = await manager.get_valid_access_token(server, user_id=USER_ID)
    finally:
        await http.aclose()

    assert token == "u-new-access"
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    fresh_access = await cred_repo.get(access_cred.id)
    assert fresh_access is not None
    assert (
        await encryption_backend.decrypt(fresh_access.value_encrypted)
    ).decode() == "u-new-access"
    fresh_refresh = await cred_repo.get(refresh_cred.id)
    assert fresh_refresh is not None
    assert (
        await encryption_backend.decrypt(fresh_refresh.value_encrypted)
    ).decode() == "u-new-refresh"

    user_repo = UserMCPCredentialRepository(session, org_id=ORG_ID)
    fresh_user = await user_repo.get(user_id=USER_ID, mcp_server_id=server.id)
    assert fresh_user is not None
    assert fresh_user.oauth_expires_at is not None
    expires_at = fresh_user.oauth_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC) + timedelta(minutes=30)


async def test_get_valid_access_token_rejects_non_oauth_server(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    server = await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id=None,
            name="static-install",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="static",
            credential_scope="org",
            credential_id=None,
            authed=True,
            created_by_user_id=USER_ID,
        )
    )
    handler = _ScriptedHandler(_metadata_handler(), token_responses=[])
    manager, http = _make_manager(
        session=session,
        fake_redis=fake_redis,
        backend=encryption_backend,
        handler=handler,
    )
    try:
        with pytest.raises(OAuthInvalidServerState):
            await manager.get_valid_access_token(server)
    finally:
        await http.aclose()
