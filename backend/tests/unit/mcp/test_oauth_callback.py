"""Unit tests for cubebox.mcp.oauth.callback."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
    server_url_hash,
)
from cubebox.mcp.exceptions import (
    OAuthCallbackError,
    OAuthInvalidServerState,
    OAuthPKCEMissing,
    OAuthStateExpired,
)
from cubebox.mcp.oauth.callback import (
    PKCE_REDIS_KEY_PREFIX,
    CallbackResult,
    OAuthCallbackHandler,
)
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models import Credential, MCPServer
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository, UserMCPCredentialRepository
from cubebox.services.credential import CredentialService

ORG_ID = "org-test"
USER_ID = "user-test"
SERVER_URL = "https://mcp.example.com"
TOKEN_ENDPOINT = "https://auth.example.com/oauth/token"
REDIRECT_URI = "https://app.example.com/api/v1/oauth/mcp/callback"
STATE_SECRET = b"unit-test-state-secret-bytes!!!!"


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


def _metadata_responses() -> dict[str, httpx.Response]:
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


class _Handler:
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


def _credential_service_factory(
    session: AsyncSession,
    backend: FernetBackend,
) -> Callable[[str | None, str | None], CredentialService]:
    def _factory(org_id: str | None, actor_user_id: str | None) -> CredentialService:
        repo = CredentialRepository(session, org_id=org_id)
        return CredentialService(repo, backend, org_id=org_id, actor_user_id=actor_user_id)

    return _factory


async def _seed_oauth_install(
    session: AsyncSession,
    *,
    credential_scope: str,
    owner_workspace_id: str | None,
) -> MCPServer:
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    server = await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id=owner_workspace_id,
            name=f"oauth-install-{credential_scope}",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope=credential_scope,
            credential_id=None,
            oauth_client_config={"client_id": "client-abc"},
            authed=False,
            created_by_user_id=USER_ID,
        )
    )
    return server


def _make_handler(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
    http: httpx.AsyncClient,
) -> OAuthCallbackHandler:
    metadata = OAuthMetadataDiscovery(http)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    return OAuthCallbackHandler(
        http_client=http,
        redis=fake_redis,
        state_store=state_store,
        metadata=metadata,
        encryption_backend=encryption_backend,
        credential_service_factory=_credential_service_factory(session, encryption_backend),
        server_repo=MCPServerRepository(session, org_id=ORG_ID),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ORG_ID),
        redirect_uri=REDIRECT_URI,
    )


async def _stub_discovery_success() -> None:
    """Patch ``discover_tools`` so the post-callback tool refresh succeeds."""


async def test_handle_callback_org_scope_writes_vault_and_flips_authed(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )

    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-abc")

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)

    async def _fake_discover(
        server: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [{"name": "search", "description": "", "input_schema": {}}], None

    with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
        try:
            result = await cb_handler.handle_callback(state=state, code="auth-code-1")
        finally:
            await http.aclose()

    assert isinstance(result, CallbackResult)
    assert result.install_id == server.id
    assert result.authed is True

    # Token endpoint was hit with the right body.
    assert len(handler.token_calls) == 1
    body = handler.token_calls[0]["body"]
    assert "grant_type=authorization_code" in body
    assert "code=auth-code-1" in body
    assert "code_verifier=verifier-abc" in body
    assert "client_id=client-abc" in body

    # PKCE verifier was deleted.
    assert await fake_redis.get(PKCE_REDIS_KEY_PREFIX + server.id) is None

    # Server is authed and tracks both vault rows + expires_at.
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    assert fresh.authed is True
    assert fresh.credential_id is not None
    assert "refresh_token_credential_id" in fresh.oauth_client_config
    assert "expires_at" in fresh.oauth_client_config

    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    access_cred = await cred_repo.get(fresh.credential_id)
    refresh_cred = await cred_repo.get(fresh.oauth_client_config["refresh_token_credential_id"])
    assert access_cred is not None and refresh_cred is not None
    assert access_cred.kind == CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
    assert refresh_cred.kind == CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN
    assert (
        await encryption_backend.decrypt(access_cred.value_encrypted)
    ).decode() == "fresh-access"
    assert (
        await encryption_backend.decrypt(refresh_cred.value_encrypted)
    ).decode() == "fresh-refresh"


async def test_handle_callback_org_scope_re_oauth_rotates_existing_credentials(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """A second OAuth callback for an already-authorized install must
    rotate the vault rows in place, not insert duplicates.

    Regression: before the fix, ``_persist_org`` always called
    ``cred_service.create`` with a fixed ``(kind, name)``, and the
    second callback crashed with ``UniqueViolation`` on
    ``uq_credential_org_kind_name``. The admin Re-authenticate flow
    landed exactly on this path.
    """
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )

    async def _fake_discover(
        _server: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [], None

    async def _run_callback(token_payload: dict[str, Any]) -> CallbackResult:
        state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
        state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
        await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-x")
        handler = _Handler(
            _metadata_responses(), token_responses=[httpx.Response(200, json=token_payload)]
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
        try:
            with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
                return await cb_handler.handle_callback(state=state, code="code")
        finally:
            await http.aclose()

    await _run_callback(
        {
            "access_token": "access-v1",
            "refresh_token": "refresh-v1",
            "expires_in": 3600,
        }
    )

    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    after_first = await server_repo.get(server.id)
    assert after_first is not None
    first_access_id = after_first.credential_id
    first_refresh_id = after_first.oauth_client_config["refresh_token_credential_id"]
    assert first_access_id is not None
    assert first_refresh_id is not None

    # Re-authenticate. The AS issues new tokens; the callback runs again.
    result = await _run_callback(
        {
            "access_token": "access-v2",
            "refresh_token": "refresh-v2",
            "expires_in": 3600,
        }
    )
    assert result.authed is True

    after_second = await server_repo.get(server.id)
    assert after_second is not None
    assert after_second.credential_id == first_access_id, (
        "access credential row must be rotated, not duplicated"
    )
    assert after_second.oauth_client_config["refresh_token_credential_id"] == first_refresh_id, (
        "refresh credential row must be rotated, not duplicated"
    )

    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    access_cred = await cred_repo.get(first_access_id)
    refresh_cred = await cred_repo.get(first_refresh_id)
    assert access_cred is not None and refresh_cred is not None
    assert (await encryption_backend.decrypt(access_cred.value_encrypted)).decode() == "access-v2"
    assert (await encryption_backend.decrypt(refresh_cred.value_encrypted)).decode() == "refresh-v2"


async def test_handle_callback_preserves_existing_refresh_token_when_as_omits_it(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """RFC 6749 §6: when the token response omits ``refresh_token``, the
    previously issued refresh token remains valid. The callback must not
    drop ``refresh_token_credential_id`` from ``oauth_client_config`` —
    otherwise the next access-token expiry raises ``OAuthInvalidServerState``
    instead of refreshing.
    """
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )

    async def _fake_discover(
        _server: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [], None

    async def _run_callback(token_payload: dict[str, Any]) -> None:
        state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
        state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
        await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-x")
        handler = _Handler(
            _metadata_responses(),
            token_responses=[httpx.Response(200, json=token_payload)],
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
        try:
            with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
                await cb_handler.handle_callback(state=state, code="code")
        finally:
            await http.aclose()

    # First callback: AS returns both tokens, refresh credential row is created.
    await _run_callback(
        {"access_token": "access-v1", "refresh_token": "refresh-v1", "expires_in": 3600}
    )
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    after_first = await server_repo.get(server.id)
    assert after_first is not None
    first_refresh_id = after_first.oauth_client_config["refresh_token_credential_id"]
    assert first_refresh_id is not None

    # Second callback: AS rotates access_token only — RFC 6749 §6 permits
    # omitting refresh_token, in which case the previous one stays valid.
    await _run_callback({"access_token": "access-v2", "expires_in": 3600})

    after_second = await server_repo.get(server.id)
    assert after_second is not None
    assert (
        after_second.oauth_client_config.get("refresh_token_credential_id") == first_refresh_id
    ), "must preserve refresh_token_credential_id when AS omits refresh_token"

    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    refresh_cred = await cred_repo.get(first_refresh_id)
    assert refresh_cred is not None
    assert (
        await encryption_backend.decrypt(refresh_cred.value_encrypted)
    ).decode() == "refresh-v1", "underlying refresh credential row must be untouched"


async def test_handle_callback_user_scope_writes_user_credential_row(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(
        session,
        credential_scope="user",
        owner_workspace_id="ws-test",
    )

    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-user")

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "u-access",
                    "refresh_token": "u-refresh",
                    "expires_in": 1800,
                },
            )
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)

    async def _fake_discover(
        server: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [], None

    with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
        try:
            result = await cb_handler.handle_callback(state=state, code="auth-code-u")
        finally:
            await http.aclose()

    assert result.authed is True
    user_repo = UserMCPCredentialRepository(session, org_id=ORG_ID)
    user_cred = await user_repo.get(user_id=USER_ID, mcp_server_id=server.id)
    assert user_cred is not None
    assert user_cred.credential_id is not None
    assert user_cred.oauth_refresh_token_credential_id is not None
    assert user_cred.oauth_expires_at is not None
    expires_at = user_cred.oauth_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC) + timedelta(minutes=20)

    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    access_cred = await cred_repo.get(user_cred.credential_id)
    assert access_cred is not None
    assert access_cred.kind == CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN

    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    assert fresh.authed is True
    # User-scope path does NOT write to server.credential_id.
    assert fresh.credential_id is None


async def test_handle_callback_invalid_state_bubbles(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    # Consume it once so the next consume raises Expired.
    await state_store.consume(state)

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
    try:
        with pytest.raises(OAuthStateExpired):
            await cb_handler.handle_callback(state=state, code="x")
    finally:
        await http.aclose()


async def test_handle_callback_pkce_missing_raises(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    # PKCE not written to redis on purpose.

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
    try:
        with pytest.raises(OAuthPKCEMissing):
            await cb_handler.handle_callback(state=state, code="x")
    finally:
        await http.aclose()


async def test_handle_callback_token_endpoint_400_raises_callback_error(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier")

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "code already redeemed",
                },
            )
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
    try:
        with pytest.raises(OAuthCallbackError) as excinfo:
            await cb_handler.handle_callback(state=state, code="bad")
    finally:
        await http.aclose()

    assert excinfo.value.status == 400
    assert excinfo.value.error == "invalid_grant"
    assert excinfo.value.error_description == "code already redeemed"


async def test_handle_callback_unknown_install_raises_invalid_server_state(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id="mcp-doesnotexist", actor_user_id=USER_ID)

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)
    try:
        with pytest.raises(OAuthInvalidServerState):
            await cb_handler.handle_callback(state=state, code="x")
    finally:
        await http.aclose()


async def test_handle_callback_confidential_client_uses_basic_auth(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """Confidential clients (DCR or static catalog secret) must HTTP Basic-auth
    the token-exchange request with the client_secret pulled from the vault.

    Regression guard for the wrong-credential-kind bug: ``callback.py`` used
    to request the secret with ``kind=MCP_OAUTH_ACCESS_TOKEN``, which always
    raised ``CredentialKindMismatch`` and broke every confidential-client
    token exchange.
    """
    server = await _seed_oauth_install(
        session,
        credential_scope="org",
        owner_workspace_id=None,
    )

    # Seed a vault row holding the per-install client_secret under the
    # right kind. Mirrors what start.py writes after RFC 7591 DCR or
    # what catalog_seed seeds for static-client connectors.
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    cred_service = CredentialService(
        cred_repo, encryption_backend, org_id=ORG_ID, actor_user_id=USER_ID
    )
    secret_credential_id = await cred_service.create(
        kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
        name=f"mcp_oauth_client_secret:{server.id}",
        plaintext="topsecret-shh",
    )
    server.oauth_client_config = {
        "client_id": "client-abc",
        "client_secret_credential_id": secret_credential_id,
    }
    await MCPServerRepository(session, org_id=ORG_ID).update(server)

    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-conf")

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "conf-access",
                    "refresh_token": "conf-refresh",
                    "expires_in": 3600,
                },
            )
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_handler(session, fake_redis, encryption_backend, http)

    async def _fake_discover(
        server: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [], None

    with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
        try:
            result = await cb_handler.handle_callback(state=state, code="auth-code-conf")
        finally:
            await http.aclose()

    assert result.authed is True

    # Outgoing token request carried Basic auth derived from client_id +
    # the decrypted vault secret. With the bug present, this assert never
    # runs because handle_callback raises CredentialKindMismatch.
    assert len(handler.token_calls) == 1
    headers = handler.token_calls[0]["headers"]
    auth_header = headers.get("authorization") or headers.get("Authorization")
    assert auth_header is not None and auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.removeprefix("Basic ").encode("ascii")).decode()
    assert decoded == "client-abc:topsecret-shh"


# ---- ensure unused fixtures lint-clean ----
_ = Credential  # keep import alive for future test extensions
