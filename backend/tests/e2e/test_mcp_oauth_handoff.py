"""E2E for MCP install → authentication handoff."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.config import config as _cubebox_config
from cubebox.credentials.encryption import FernetBackend
from cubebox.db.engine import _build_database_url
from cubebox.mcp._constants import server_url_hash
from cubebox.mcp.oauth.callback import OAuthCallbackHandler
from cubebox.mcp.oauth.dcr import DCRClient
from cubebox.mcp.oauth.metadata import (
    AuthorizationServerMetadata,
    ProtectedResourceMetadata,
)
from cubebox.mcp.oauth.start import OAuthStartResult, OAuthStartService
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models.mcp import MCPConnectorInstall
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPCredentialGrantRepository,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Direct DB session helpers (these tests poke models directly).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def db_session(
    db_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_maker() as session:
        yield session


# ---------------------------------------------------------------------------
# Seed: brand-new org + workspace + user (no FastAPI lifespan needed).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_org_workspace_user(
    db_session_maker: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str]:
    """Returns ``(org_id, workspace_id, user_id)``."""
    import secrets

    from fastapi_users.db import SQLAlchemyUserDatabase
    from fastapi_users.schemas import BaseUserCreate

    from cubebox.auth.users import UserManager, _slugify_org_name
    from cubebox.models import User
    from cubebox.repositories import (
        MembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    async with db_session_maker() as session:
        org_repo = OrganizationRepository(session)
        ws_repo = WorkspaceRepository(session)
        mem_repo = MembershipRepository(session)
        email = f"oauth-handoff-{secrets.token_hex(4)}@example.com"
        password = secrets.token_urlsafe(16)
        org_name = f"Org {email}"
        org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
        ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
        from cubebox.models import Role

        await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
        await session.commit()
        return org.id, ws.id, user.id


# ---------------------------------------------------------------------------
# Seed: OAuth install (caller picks the policy).
# ---------------------------------------------------------------------------


async def _seed_oauth_install(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str | None,
    install_scope: str = "workspace",
    default_credential_policy: str = "user",
    created_by_user_id: str | None = None,
) -> MCPConnectorInstall:
    """Insert a directly-installed (template_id=NULL) OAuth install row.

    Pre-populates ``oauth_client_config.client_id`` so the start service
    short-circuits DCR.
    """
    from datetime import UTC, datetime

    install = MCPConnectorInstall(
        org_id=org_id,
        workspace_id=workspace_id,
        install_scope=install_scope,
        template_id=None,
        name="oauth-e2e-install",
        server_url="https://oauth-e2e.example.com/mcp",
        server_url_hash=server_url_hash("https://oauth-e2e.example.com/mcp"),
        transport="streamable_http",
        auth_method="oauth",
        default_credential_policy=default_credential_policy,
        auth_status="pending",
        install_state="active",
        oauth_client_config={"client_id": "test-client-id"},
        created_by_user_id=created_by_user_id or "usr-test-actor",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(install)
    await session.commit()
    await session.refresh(install)
    return install


@pytest_asyncio.fixture
async def seeded_oauth_install(
    db_session_maker: async_sessionmaker[AsyncSession],
    seed_org_workspace_user: tuple[str, str, str],
) -> tuple[str, str, str, str]:
    """Yield ``(install_id, grant_scope, workspace_id, user_id)`` for a user-policy OAuth install."""
    org_id, ws_id, user_id = seed_org_workspace_user
    async with db_session_maker() as session:
        install = await _seed_oauth_install(
            session,
            org_id=org_id,
            workspace_id=ws_id,
            install_scope="workspace",
            default_credential_policy="user",
            created_by_user_id=user_id,
        )
        return install.id, "user", ws_id, user_id


# ---------------------------------------------------------------------------
# Fake AS metadata + injected DCR / discovery.
# ---------------------------------------------------------------------------


_FAKE_AS_METADATA = AuthorizationServerMetadata(
    issuer="https://oauth-e2e.example.com",
    authorization_endpoint="https://oauth-e2e.example.com/authorize",
    token_endpoint="https://oauth-e2e.example.com/token",
    revocation_endpoint=None,
    registration_endpoint=None,
    code_challenge_methods_supported=["S256"],
    grant_types_supported=["authorization_code", "refresh_token"],
    response_types_supported=["code"],
    scopes_supported=["read"],
    raw={},
)

_FAKE_PR_METADATA = ProtectedResourceMetadata(
    resource="https://oauth-e2e.example.com/mcp",
    authorization_servers=["https://oauth-e2e.example.com"],
)


class _StubDiscovery:
    """Stand-in for OAuthMetadataDiscovery that bypasses HTTP."""

    async def discover_for_resource(
        self, resource_url: str
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        return _FAKE_PR_METADATA, _FAKE_AS_METADATA


# ---------------------------------------------------------------------------
# OAuthStartService fixture.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield fake
    finally:
        await fake.aclose()


@pytest_asyncio.fixture
async def oauth_state_store(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> OAuthStateStore:
    secret = str(_cubebox_config.get("auth.csrf_secret", "test-csrf-secret")).encode("utf-8")
    return OAuthStateStore(redis=fake_redis, secret_key=secret, ttl_seconds=300)


@pytest_asyncio.fixture
async def encryption_backend() -> FernetBackend:
    key = "Nmu-K8QhP_uhdjmwbaiNmgxVQHbGeCkMOCz8RKp1LMM="
    return FernetBackend([key.encode("utf-8")])


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        yield c


@pytest_asyncio.fixture
async def oauth_start_service(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
) -> OAuthStartService:
    metadata: Any = _StubDiscovery()
    dcr = DCRClient(http_client)
    return OAuthStartService(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=metadata,
        dcr=dcr,
        http_client=http_client,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_start_oauth_flow_returns_authorize_url_state_and_expires_at(
    oauth_start_service: OAuthStartService,
    seeded_oauth_install: tuple[str, str, str, str],
) -> None:
    install_id, scope, ws_id, user_id = seeded_oauth_install
    result = await oauth_start_service.start_oauth_flow(
        install_id=install_id,
        actor_user_id=user_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert isinstance(result, OAuthStartResult)
    assert result.authorize_url.startswith("https://")
    # state is opaque but must round-trip through OAuthStateStore.consume.
    assert "." in result.state  # payload.signature shape
    assert result.expires_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Task 2: callback handler fixtures and tests.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_oauth_org_install(
    db_session_maker: async_sessionmaker[AsyncSession],
    seed_org_workspace_user: tuple[str, str, str],
) -> tuple[str, str, str]:
    """Yield ``(install_id, org_id, workspace_id)`` for an org-policy OAuth install."""
    org_id, ws_id, user_id = seed_org_workspace_user
    async with db_session_maker() as session:
        install = await _seed_oauth_install(
            session,
            org_id=org_id,
            workspace_id=None,
            install_scope="org",
            default_credential_policy="org",
            created_by_user_id=user_id,
        )
        return install.id, org_id, ws_id


@pytest_asyncio.fixture
async def oauth_callback_handler(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
) -> OAuthCallbackHandler:
    metadata: Any = _StubDiscovery()
    return OAuthCallbackHandler(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=metadata,
        http_client=http_client,
    )


@pytest_asyncio.fixture
async def install_repo(
    db_session: AsyncSession,
    seed_org_workspace_user: tuple[str, str, str],
) -> MCPConnectorInstallRepository:
    org_id, _ws_id, _user_id = seed_org_workspace_user
    return MCPConnectorInstallRepository(db_session, org_id=org_id)


@pytest_asyncio.fixture
async def grant_repo(
    db_session: AsyncSession,
    seed_org_workspace_user: tuple[str, str, str],
) -> MCPCredentialGrantRepository:
    org_id, _ws_id, _user_id = seed_org_workspace_user
    return MCPCredentialGrantRepository(db_session, org_id=org_id)


class _FakeTokenResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


async def test_callback_writes_user_grant_and_keeps_install_pending(
    oauth_callback_handler: OAuthCallbackHandler,
    oauth_start_service: OAuthStartService,
    grant_repo: MCPCredentialGrantRepository,
    install_repo: MCPConnectorInstallRepository,
    seeded_oauth_install: tuple[str, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-policy install: grant lands at scope='user', auth_status STAYS
    'pending' (per spec §6 — auth_status is per-install, not per-user)."""
    install_id, scope, ws_id, user_id = seeded_oauth_install
    assert scope == "user"

    start = await oauth_start_service.start_oauth_flow(
        install_id=install_id,
        actor_user_id=user_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )

    async def fake_post_token(
        _self: OAuthCallbackHandler,
        _install: MCPConnectorInstall,
        _code: str,
        _verifier: str,
        _cred_service: Any,
    ) -> dict[str, Any]:
        return {
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(OAuthCallbackHandler, "_post_token_exchange", fake_post_token)

    result = await oauth_callback_handler.handle_callback(
        state=start.state,
        code="fake-code",
    )

    assert result.status == "ok"
    assert result.install_id == install_id
    assert result.state == start.state

    grant = await grant_repo.get_user_grant(install_id, user_id, workspace_id=ws_id)
    assert grant is not None
    assert grant.grant_status == "valid"

    install = await install_repo.get(install_id)
    assert install is not None
    assert install.auth_status == "pending"  # user-policy: never flips


async def test_callback_writes_org_grant_and_authorizes_install(
    oauth_callback_handler: OAuthCallbackHandler,
    oauth_start_service: OAuthStartService,
    grant_repo: MCPCredentialGrantRepository,
    install_repo: MCPConnectorInstallRepository,
    seeded_oauth_org_install: tuple[str, str, str],
    seed_org_workspace_user: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-policy install: grant lands at scope='org', auth_status flips
    'pending' → 'authorized' because rule §6 fires."""
    install_id, _org_id, _ws_id = seeded_oauth_org_install
    _seed_org_id, _seed_ws_id, actor_user_id = seed_org_workspace_user

    start = await oauth_start_service.start_oauth_flow(
        install_id=install_id,
        actor_user_id=actor_user_id,
        grant_scope="org",
        workspace_id=None,
        user_id=None,
    )

    async def fake_post_token(
        _self: OAuthCallbackHandler,
        _install: MCPConnectorInstall,
        _code: str,
        _verifier: str,
        _cred_service: Any,
    ) -> dict[str, Any]:
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    monkeypatch.setattr(OAuthCallbackHandler, "_post_token_exchange", fake_post_token)

    result = await oauth_callback_handler.handle_callback(
        state=start.state,
        code="fake-code",
    )
    assert result.status == "ok"

    grant = await grant_repo.get_org_grant(install_id)
    assert grant is not None
    assert grant.grant_status == "valid"

    install = await install_repo.get(install_id)
    assert install is not None
    assert install.auth_status == "authorized"
