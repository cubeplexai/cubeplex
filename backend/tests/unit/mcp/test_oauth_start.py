"""Unit tests for ``cubebox.mcp.oauth.start.OAuthStartService``.

Covered behaviors:

- DCR path: registers a client, persists secret in vault, snapshots
  AS endpoints onto the install row.
- Static path: uses the catalog's ``oauth_static_client_id``.
- Idempotency: skips DCR when ``oauth_client_config`` already has
  a ``client_id``.
- PKCE verifier stored under ``mcp_oauth_pkce:{install_id}`` (TTL set);
  state token issued + redis-bound; callback ticket bound to actor.
- Authorize URL contains the expected RFC 6749 / RFC 7636 params.
- Cross-org access is rejected with ``MCPServerNotFound``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
    server_url_hash,
)
from cubebox.mcp.exceptions import MCPServerNotFound, OAuthInvalidServerState
from cubebox.mcp.oauth.callback import PKCE_REDIS_KEY_PREFIX
from cubebox.mcp.oauth.dcr import DCRClient
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.start import (
    CALLBACK_TICKET_REDIS_KEY_PREFIX,
    OAuthStartService,
)
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models import MCPCatalogConnector, MCPServer
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService

ORG_ID = "org-test"
USER_ID = "user-test"
SERVER_URL = "https://mcp.example.com"
AUTHORIZE_ENDPOINT = "https://auth.example.com/oauth/authorize"
TOKEN_ENDPOINT = "https://auth.example.com/oauth/token"
REGISTRATION_ENDPOINT = "https://auth.example.com/oauth/register"
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


def _well_known_resource_url(base: str) -> str:
    return f"{base.rstrip('/')}/.well-known/oauth-protected-resource"


def _well_known_as_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"


def _metadata_responses(*, registration: bool) -> dict[str, httpx.Response]:
    as_meta: dict[str, Any] = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": AUTHORIZE_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
    }
    if registration:
        as_meta["registration_endpoint"] = REGISTRATION_ENDPOINT
    return {
        _well_known_resource_url(SERVER_URL): httpx.Response(
            200,
            json={
                "resource": SERVER_URL,
                "authorization_servers": ["https://auth.example.com"],
            },
        ),
        _well_known_as_url("https://auth.example.com"): httpx.Response(200, json=as_meta),
    }


class _Handler:
    def __init__(
        self,
        baseline: dict[str, httpx.Response],
        *,
        registration_response: httpx.Response | None = None,
    ) -> None:
        self._baseline = baseline
        self._registration_response = registration_response
        self.registration_calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == REGISTRATION_ENDPOINT:
            self.registration_calls.append(
                {
                    "method": request.method,
                    "headers": dict(request.headers),
                    "body": request.read().decode("utf-8"),
                }
            )
            if self._registration_response is None:
                return httpx.Response(500, text="no scripted registration response")
            return self._registration_response
        if url in self._baseline:
            return self._baseline[url]
        return httpx.Response(404, json={"error": "not_found"})


async def _seed_connector(
    session: AsyncSession,
    *,
    dcr_supported: bool,
    static_client_id: str | None,
) -> MCPCatalogConnector:
    connector = MCPCatalogConnector(
        slug="example",
        name="Example MCP",
        provider="example",
        description="test",
        server_url=SERVER_URL,
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=dcr_supported,
        oauth_default_scope="read write",
        oauth_static_client_id=static_client_id,
        status="active",
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def _seed_install(
    session: AsyncSession,
    *,
    connector_id: str,
    org_id: str = ORG_ID,
    owner_workspace_id: str | None = None,
    oauth_client_config: dict[str, Any] | None = None,
) -> MCPServer:
    server_repo = MCPServerRepository(session, org_id=org_id)
    server = await server_repo.add(
        MCPServer(
            org_id=org_id,
            owner_workspace_id=owner_workspace_id,
            name=f"oauth-install-{owner_workspace_id or 'org'}",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="org" if owner_workspace_id is None else "user",
            credential_id=None,
            catalog_connector_id=connector_id,
            oauth_client_config=oauth_client_config or {},
            authed=False,
            created_by_user_id=USER_ID,
        )
    )
    return server


def _make_service(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
    http: httpx.AsyncClient,
    *,
    org_id: str = ORG_ID,
) -> OAuthStartService:
    metadata = OAuthMetadataDiscovery(http)
    dcr = DCRClient(http)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    cred_repo = CredentialRepository(session, org_id=org_id)
    cred_service = CredentialService(
        cred_repo,
        encryption_backend,
        org_id=org_id,
        actor_user_id=USER_ID,
    )
    return OAuthStartService(
        server_repo=MCPServerRepository(session, org_id=org_id),
        catalog_repo=MCPCatalogConnectorRepository(session),
        metadata=metadata,
        dcr_client=dcr,
        state_store=state_store,
        credential_service=cred_service,
        redis=fake_redis,
        redirect_uri=REDIRECT_URI,
        org_id=org_id,
    )


# ---------------- DCR path ---------------- #


async def test_start_dcr_path_registers_client_and_persists_oauth_client_config(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=True, static_client_id=None)
    server = await _seed_install(session, connector_id=connector.id)

    handler = _Handler(
        _metadata_responses(registration=True),
        registration_response=httpx.Response(
            201,
            json={
                "client_id": "dcr-client-123",
                "client_secret": "dcr-secret",
                "client_id_issued_at": 0,
            },
        ),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        result = await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()

    # Registration was hit exactly once.
    assert len(handler.registration_calls) == 1

    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    cfg = fresh.oauth_client_config
    assert cfg["client_id"] == "dcr-client-123"
    assert cfg["authorization_endpoint"] == AUTHORIZE_ENDPOINT
    assert cfg["token_endpoint"] == TOKEN_ENDPOINT
    assert cfg["registration_endpoint"] == REGISTRATION_ENDPOINT
    assert cfg["scope"] == "read write"
    secret_id = cfg["client_secret_credential_id"]
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    secret_cred = await cred_repo.get(secret_id)
    assert secret_cred is not None
    assert secret_cred.kind == CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET
    assert (await encryption_backend.decrypt(secret_cred.value_encrypted)).decode() == "dcr-secret"

    # The result wires the cookie value the route layer is expected to set.
    assert result.cookie_value
    assert result.state
    assert result.authorize_url.startswith(AUTHORIZE_ENDPOINT)


async def test_start_static_path_uses_catalog_static_client_id(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(
        session,
        dcr_supported=False,
        static_client_id="static-app-id",
    )
    server = await _seed_install(session, connector_id=connector.id)

    handler = _Handler(_metadata_responses(registration=False))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        result = await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()

    assert handler.registration_calls == []  # no DCR call

    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    assert fresh.oauth_client_config["client_id"] == "static-app-id"
    assert fresh.oauth_client_config["scope"] == "read write"

    parsed = urlparse(result.authorize_url)
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["static-app-id"]


async def test_start_idempotent_when_oauth_client_config_already_has_client_id(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=True, static_client_id=None)
    server = await _seed_install(
        session,
        connector_id=connector.id,
        oauth_client_config={"client_id": "already-registered"},
    )

    # No registration response scripted: any DCR call would 500 in the
    # handler, which would surface as DCRError. This test passes only if
    # DCR is skipped.
    handler = _Handler(_metadata_responses(registration=True))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        result = await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()

    assert handler.registration_calls == []
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    fresh = await server_repo.get(server.id)
    assert fresh is not None
    assert fresh.oauth_client_config["client_id"] == "already-registered"

    parsed = urlparse(result.authorize_url)
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["already-registered"]


# ---------------- Redis side effects ---------------- #


async def test_start_writes_pkce_to_redis_and_issues_state_and_ticket(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=False, static_client_id="static-1")
    server = await _seed_install(session, connector_id=connector.id)

    handler = _Handler(_metadata_responses(registration=False))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        result = await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()

    # PKCE verifier persisted with TTL.
    verifier = await fake_redis.get(PKCE_REDIS_KEY_PREFIX + server.id)
    assert verifier is not None
    pkce_ttl = await fake_redis.ttl(PKCE_REDIS_KEY_PREFIX + server.id)
    assert 0 < pkce_ttl <= 300

    # Ticket binds to actor.
    ticket_value = await fake_redis.get(CALLBACK_TICKET_REDIS_KEY_PREFIX + result.cookie_value)
    assert ticket_value == USER_ID
    ticket_ttl = await fake_redis.ttl(CALLBACK_TICKET_REDIS_KEY_PREFIX + result.cookie_value)
    assert 0 < ticket_ttl <= 600

    # State is consumable via the same store and yields the actor + install.
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    payload = await state_store.consume(result.state)
    assert payload.install_id == server.id
    assert payload.actor_user_id == USER_ID


async def test_start_authorize_url_contains_expected_params(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=False, static_client_id="static-1")
    server = await _seed_install(session, connector_id=connector.id)

    handler = _Handler(_metadata_responses(registration=False))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        result = await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()

    parsed = urlparse(result.authorize_url)
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["static-1"]
    assert qs["redirect_uri"] == [REDIRECT_URI]
    assert qs["state"] == [result.state]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["scope"] == ["read write"]
    # PKCE challenge is present and a base64url short string (not the verifier).
    assert qs["code_challenge"]
    raw_verifier = await fake_redis.get(PKCE_REDIS_KEY_PREFIX + server.id)
    assert qs["code_challenge"][0] != raw_verifier


async def test_start_rejects_cross_org_install(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=False, static_client_id="static-1")
    # Install owned by org-A, but service scoped to org-B.
    server = await _seed_install(session, connector_id=connector.id, org_id="org-A")

    handler = _Handler(_metadata_responses(registration=False))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(
        session,
        fake_redis,
        encryption_backend,
        http,
        org_id="org-B",
    )
    try:
        with pytest.raises(MCPServerNotFound):
            await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()


async def test_start_rejects_non_oauth_install(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    connector = await _seed_connector(session, dcr_supported=False, static_client_id="static-1")
    # Build a static install with the same row shape — only auth_method differs.
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    server = await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            name="non-oauth",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="static",
            credential_scope="org",
            credential_id=None,
            catalog_connector_id=connector.id,
            authed=False,
            created_by_user_id=USER_ID,
        )
    )

    handler = _Handler(_metadata_responses(registration=False))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _make_service(session, fake_redis, encryption_backend, http)
    try:
        with pytest.raises(OAuthInvalidServerState):
            await svc.start(install_id=server.id, actor_user_id=USER_ID)
    finally:
        await http.aclose()
