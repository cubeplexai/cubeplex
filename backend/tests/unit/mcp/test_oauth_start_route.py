"""Unit tests for the workspace ``POST /api/v1/ws/{ws}/mcp/installs/{id}/oauth/start`` route.

These tests cover the route's creator-only / ownership pre-validation
that runs *before* any AS or OAuth logic. We exercise the full route
with httpx ``ASGITransport`` and DI overrides for ``require_member`` +
``get_oauth_start_service_member``, with a real ``MCPServerRepository``
backed by an in-memory SQLite database.

Behaviors covered:

- 404 ``mcp_oauth.install_not_found`` when the workspace path param does
  not match the install's ``owner_workspace_id`` (cross-workspace).
- 403 ``mcp_oauth.permission_denied`` when the caller is not the install
  creator (different user, same workspace).

The pre-validation rejects before any HTTP call, so the OAuthStartService
is wired with no AS/redis interactions — its dependencies just need to
exist for FastAPI to resolve the route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.api.routes.v1 import mcp_oauth as mcp_oauth_routes
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp._constants import server_url_hash
from cubebox.mcp.dependencies import get_oauth_start_service_member
from cubebox.mcp.oauth.dcr import DCRClient
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.start import OAuthStartService
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models import MCPServer, Role, User
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService

ORG_ID = "org-test"
CREATOR_USER_ID = "usr-creator"
OTHER_USER_ID = "usr-other"
WS_OWNER = "ws-owner"
WS_OTHER = "ws-other"
SERVER_URL = "https://mcp.example.com"
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


def _make_user(user_id: str) -> User:
    """Build a minimal User instance suitable for RequestContext."""
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        hashed_password="x",
    )


async def _seed_workspace_install(
    session: AsyncSession,
    *,
    owner_workspace_id: str,
    created_by_user_id: str,
) -> MCPServer:
    """Insert a workspace-private OAuth install into the in-memory DB."""
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    return await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id=owner_workspace_id,
            name=f"oauth-install-{owner_workspace_id}",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="user",
            credential_id=None,
            oauth_client_config={"client_id": "client-abc"},
            authed=False,
            created_by_user_id=created_by_user_id,
        )
    )


def _build_start_service(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
    http: httpx.AsyncClient,
) -> OAuthStartService:
    """Construct an OAuthStartService that won't be reached by the test.

    The 404/403 pre-validation rejects before ``svc.start()`` runs, but
    the route still needs the dependency to resolve. We stand the service
    up with a real (org-scoped) ``MCPServerRepository`` so that the route
    can call ``svc.server_repo.get(install_id)`` for the pre-validation
    branch.
    """
    metadata = OAuthMetadataDiscovery(http)
    dcr = DCRClient(http)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    cred_repo = CredentialRepository(session, org_id=ORG_ID)
    cred_service = CredentialService(
        cred_repo,
        encryption_backend,
        org_id=ORG_ID,
        actor_user_id=CREATOR_USER_ID,
    )
    return OAuthStartService(
        server_repo=MCPServerRepository(session, org_id=ORG_ID),
        catalog_repo=MCPCatalogConnectorRepository(session),
        metadata=metadata,
        dcr_client=dcr,
        state_store=state_store,
        credential_service=cred_service,
        redis=fake_redis,
        redirect_uri=REDIRECT_URI,
        org_id=ORG_ID,
    )


def _build_app(
    *,
    ctx: RequestContext,
    svc: OAuthStartService,
) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_oauth_routes.oauth_member_router, prefix="/api/v1")

    async def _override_member() -> Any:
        return ctx

    async def _override_start_service() -> Any:
        return svc

    app.dependency_overrides[require_member] = _override_member
    app.dependency_overrides[get_oauth_start_service_member] = _override_start_service
    return app


async def _hit_start(
    app: FastAPI,
    *,
    workspace_id: str,
    install_id: str,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/oauth/start",
            json={},
        )


# ---------------- 404 cross-workspace ---------------- #


async def test_workspace_start_returns_404_when_install_owner_workspace_mismatches_path(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """Member in WS_OTHER tries to start OAuth on an install owned by WS_OWNER."""
    server = await _seed_workspace_install(
        session,
        owner_workspace_id=WS_OWNER,
        created_by_user_id=CREATOR_USER_ID,
    )
    # Caller is a member of a *different* workspace in the same org.
    ctx = RequestContext(
        user=_make_user(CREATOR_USER_ID),
        org_id=ORG_ID,
        workspace_id=WS_OTHER,
        role=Role.MEMBER,
    )

    handler_calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(request)
        return httpx.Response(500, text="route should never reach OAuth IO")

    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    svc = _build_start_service(session, fake_redis, encryption_backend, http)
    app = _build_app(ctx=ctx, svc=svc)
    try:
        response = await _hit_start(app, workspace_id=WS_OTHER, install_id=server.id)
    finally:
        await http.aclose()

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["detail"]["code"] == "mcp_oauth.install_not_found"
    # Pre-validation must reject before any outbound AS call.
    assert handler_calls == []


# ---------------- 403 non-creator ---------------- #


async def test_workspace_start_returns_403_when_caller_is_not_install_creator(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """Member B tries to start OAuth on an install in their workspace created by member A."""
    server = await _seed_workspace_install(
        session,
        owner_workspace_id=WS_OWNER,
        created_by_user_id=CREATOR_USER_ID,
    )
    # Caller is in the correct workspace but is NOT the creator.
    ctx = RequestContext(
        user=_make_user(OTHER_USER_ID),
        org_id=ORG_ID,
        workspace_id=WS_OWNER,
        role=Role.MEMBER,
    )

    handler_calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(request)
        return httpx.Response(500, text="route should never reach OAuth IO")

    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    svc = _build_start_service(session, fake_redis, encryption_backend, http)
    app = _build_app(ctx=ctx, svc=svc)
    try:
        response = await _hit_start(app, workspace_id=WS_OWNER, install_id=server.id)
    finally:
        await http.aclose()

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["detail"]["code"] == "mcp_oauth.permission_denied"
    # Pre-validation must reject before any outbound AS call.
    assert handler_calls == []
