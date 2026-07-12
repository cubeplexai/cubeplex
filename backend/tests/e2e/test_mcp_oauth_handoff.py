"""E2E for MCP install → authentication handoff.

Removed in Task 9 template-centric cutover:
- seeded_static_org_install fixture: used POST /admin/mcp/installs (removed)
- seeded_oauth_org_install_no_grant fixture: same
- test_admin_install_effective_static_org_pending: used GET /installs/{id}/effective (removed)
- test_admin_install_effective_oauth_org_pending_returns_pending_oauth: same

auth_status was removed from MCPConnector in the template-centric model (status
is now on MCPCredentialGrant). Callback tests no longer assert install.auth_status.

_seed_oauth_install now creates a real MCPConnectorTemplate first so the
template_id FK constraint is satisfied.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from cubebox.mcp.exceptions import DCRError, OAuthMetadataNotFound
from cubebox.mcp.oauth.callback import OAuthCallbackHandler
from cubebox.mcp.oauth.dcr import DCRClient, DCRRequest
from cubebox.mcp.oauth.metadata import (
    AuthorizationServerMetadata,
    ProtectedResourceMetadata,
)
from cubebox.mcp.oauth.start import OAuthStartError, OAuthStartResult, OAuthStartService
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models.mcp import MCPConnector
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
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
    template_id: str | None = None,
    install_scope: str = "workspace",
    default_credential_policy: str = "user",
    created_by_user_id: str | None = None,
) -> MCPConnector:
    """Insert an OAuth connector row backed by a real template.

    Creates an org-custom OAuth template (or reuses one if template_id is
    passed) so the ``template_id`` FK constraint is satisfied. Pre-populates
    ``oauth_client_config.client_id`` so the start service short-circuits DCR.

    ``workspace_id`` and ``install_scope`` are accepted but ignored — connectors
    are always org-scope in the template-centric model.
    """
    del workspace_id, install_scope

    if template_id is None:
        slug = f"oauth-e2e-{secrets.token_hex(6)}"
        template = await MCPConnectorTemplateRepository(session).upsert_by_slug(
            slug=slug,
            name="OAuth E2E Handoff",
            description="Auto-seeded OAuth template for handoff tests",
            provider="E2E",
            server_url="https://oauth-e2e.example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["oauth"],
            default_credential_policy=default_credential_policy,
            oauth_dcr_supported=False,
        )
        await session.commit()
        template_id = template.id

    connector = await MCPConnectorRepository(session, org_id=org_id).add(
        MCPConnector(
            org_id=org_id,
            template_id=template_id,
            name="oauth-e2e-install",
            server_url="https://oauth-e2e.example.com/mcp",
            server_url_hash=server_url_hash("https://oauth-e2e.example.com/mcp"),
            transport="streamable_http",
            default_credential_policy=default_credential_policy,
            status="active",
            oauth_client_config={"client_id": "test-client-id"},
            created_by_user_id=created_by_user_id,
        )
    )
    return connector


async def _connector_id_for_install(_session: AsyncSession, install: MCPConnector) -> str:
    return install.id


async def _seed_oauth_install_needing_dcr(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    created_by_user_id: str,
) -> MCPConnector:
    install = await _seed_oauth_install(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        default_credential_policy="workspace",
        created_by_user_id=None,
    )
    install.oauth_client_config = {}
    session.add(install)
    await session.commit()
    await session.refresh(install)
    return install


@pytest_asyncio.fixture
async def seeded_oauth_install(
    db_session_maker: async_sessionmaker[AsyncSession],
    seed_org_workspace_user: tuple[str, str, str],
) -> tuple[str, str, str, str, str, str]:
    """Yield ``(install_id, connector_id, grant_scope, workspace_id, user_id, org_id)`` for a
    user-policy OAuth install. The trailing org_id is needed by callers that
    pass ``actor_org_id`` into ``start_oauth_flow`` — the cross-tenant guard
    landed in the round-8 plan fix."""
    org_id, ws_id, user_id = seed_org_workspace_user
    async with db_session_maker() as session:
        install = await _seed_oauth_install(
            session,
            org_id=org_id,
            workspace_id=ws_id,
            default_credential_policy="user",
            created_by_user_id=user_id,
        )
        connector_id = await _connector_id_for_install(session, install)
        return install.id, connector_id, "user", ws_id, user_id, org_id


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

_FAKE_AS_METADATA_WITH_DCR = AuthorizationServerMetadata(
    issuer="https://oauth-e2e.example.com",
    authorization_endpoint="https://oauth-e2e.example.com/authorize",
    token_endpoint="https://oauth-e2e.example.com/token",
    revocation_endpoint=None,
    registration_endpoint="https://oauth-e2e.example.com/register",
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
_FAKE_PR_METADATA_WITH_SCOPES = ProtectedResourceMetadata(
    resource="https://oauth-e2e.example.com/mcp",
    authorization_servers=["https://oauth-e2e.example.com"],
    scopes_supported=["read:me", "search:confluence"],
)

_FAKE_AS_METADATA_WITHOUT_SCOPES = AuthorizationServerMetadata(
    issuer="https://oauth-e2e.example.com",
    authorization_endpoint="https://oauth-e2e.example.com/authorize",
    token_endpoint="https://oauth-e2e.example.com/token",
    revocation_endpoint=None,
    registration_endpoint=None,
    code_challenge_methods_supported=["S256"],
    grant_types_supported=["authorization_code", "refresh_token"],
    response_types_supported=["code"],
    scopes_supported=None,
    raw={},
)


class _StubDiscovery:
    """Stand-in for OAuthMetadataDiscovery that bypasses HTTP."""

    async def discover_for_resource(
        self, resource_url: str
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        return _FAKE_PR_METADATA, _FAKE_AS_METADATA


class _StubDcrDiscovery:
    """Stand-in that forces the OAuth start path through DCR."""

    async def discover_for_resource(
        self, resource_url: str
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        return _FAKE_PR_METADATA, _FAKE_AS_METADATA_WITH_DCR


class _StubResourceScopesDiscovery:
    """Simulates Atlassian: PR metadata has scopes, AS metadata omits them."""

    async def discover_for_resource(
        self, resource_url: str
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        return _FAKE_PR_METADATA_WITH_SCOPES, _FAKE_AS_METADATA_WITHOUT_SCOPES


class _TemplateMetadataFallbackDiscovery:
    """Simulates Intercom: no PR metadata, but a direct AS metadata URL works."""

    async def discover_for_resource(
        self, resource_url: str
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        raise OAuthMetadataNotFound(f"Metadata not found at {resource_url}")

    async def fetch_authorization_server_metadata_url(
        self, metadata_url: str
    ) -> AuthorizationServerMetadata:
        assert metadata_url == "https://mcp.intercom.com/.well-known/oauth-authorization-server"
        return _FAKE_AS_METADATA


class _RejectingDCRClient:
    async def register(self, registration_endpoint: str, request: DCRRequest) -> Any:
        raise DCRError(
            status=400,
            error="invalid_redirect_uri",
            error_description="Plaintext HTTP is allowed only for loopback addresses.",
        )


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
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
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
    oauth_state_store: OAuthStateStore,
    seeded_oauth_install: tuple[str, str, str, str, str, str],
) -> None:
    install_id, connector_id, scope, ws_id, user_id, org_id = seeded_oauth_install
    result = await oauth_start_service.start_oauth_flow(
        connector_id=install_id,
        actor_user_id=user_id,
        actor_org_id=org_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert isinstance(result, OAuthStartResult)
    assert result.authorize_url.startswith("https://")
    # state is opaque but must round-trip through OAuthStateStore.consume.
    assert "." in result.state  # payload.signature shape
    payload = await oauth_state_store.consume(result.state)
    assert payload.connector_id == connector_id
    assert result.expires_at.tzinfo is not None


async def test_start_oauth_flow_rejects_cross_tenant_install_id(
    oauth_start_service: OAuthStartService,
    seeded_oauth_install: tuple[str, str, str, str, str, str],
) -> None:
    """A caller from another org cannot mint a state for this install.

    Cross-org and truly-missing collapse to the same error so OAuth
    start can't be used as an org-existence oracle.
    """
    install_id, _connector_id, scope, ws_id, user_id, _real_org_id = seeded_oauth_install
    with pytest.raises(OAuthStartError, match="connector_install_not_found"):
        await oauth_start_service.start_oauth_flow(
            connector_id=install_id,
            actor_user_id=user_id,
            actor_org_id="org-someone-else",  # caller from a different org
            grant_scope=scope,
            workspace_id=ws_id,
            user_id=user_id,
        )


async def test_start_oauth_flow_maps_dcr_error_to_oauth_start_error(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
) -> None:
    import secrets

    from cubebox.repositories import OrganizationRepository, WorkspaceRepository

    suffix = secrets.token_hex(4)
    org = await OrganizationRepository(db_session).create(
        name=f"DCR Org {suffix}",
        slug=f"dcr-org-{suffix}",
    )
    ws = await WorkspaceRepository(db_session).create(org_id=org.id, name=f"DCR WS {suffix}")
    await db_session.commit()

    user_id = "usr-dcr-test-actor"
    install = await _seed_oauth_install_needing_dcr(
        db_session,
        org_id=org.id,
        workspace_id=ws.id,
        created_by_user_id=user_id,
    )
    service = OAuthStartService(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=_StubDcrDiscovery(),  # type: ignore[arg-type]
        dcr=_RejectingDCRClient(),  # type: ignore[arg-type]
        http_client=http_client,
    )

    with pytest.raises(OAuthStartError, match="Plaintext HTTP") as exc_info:
        await service.start_oauth_flow(
            connector_id=install.id,
            actor_user_id=user_id,
            actor_org_id=org.id,
            grant_scope="workspace",
            workspace_id=ws.id,
            user_id=None,
            frontend_origin="http://192.168.1.215:3000",
        )
    assert exc_info.value.code == "invalid_redirect_uri"
    assert exc_info.value.message == (
        "invalid_redirect_uri: Plaintext HTTP is allowed only for loopback addresses."
    )


async def test_start_oauth_flow_uses_template_authorization_server_metadata_url_fallback(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
) -> None:
    import secrets

    from cubebox.repositories import OrganizationRepository, WorkspaceRepository

    suffix = secrets.token_hex(4)
    org = await OrganizationRepository(db_session).create(
        name=f"Intercom Org {suffix}",
        slug=f"intercom-org-{suffix}",
    )
    ws = await WorkspaceRepository(db_session).create(
        org_id=org.id,
        name=f"Intercom WS {suffix}",
    )
    template = await MCPConnectorTemplateRepository(db_session).upsert_by_slug(
        slug=f"intercom-test-{suffix}",
        name="Intercom Test",
        description="Intercom test connector",
        provider="Intercom",
        server_url="https://mcp.intercom.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        template_metadata={
            "oauth_authorization_server_metadata_url": (
                "https://mcp.intercom.com/.well-known/oauth-authorization-server"
            )
        },
    )
    await db_session.commit()

    user_id = "usr-intercom-actor"
    install = await _seed_oauth_install(
        db_session,
        org_id=org.id,
        workspace_id=ws.id,
        template_id=template.id,
        install_scope="workspace",
        default_credential_policy="workspace",
        created_by_user_id=None,
    )
    service = OAuthStartService(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=_TemplateMetadataFallbackDiscovery(),  # type: ignore[arg-type]
        dcr=DCRClient(http_client),
        http_client=http_client,
    )

    result = await service.start_oauth_flow(
        connector_id=install.id,
        actor_user_id=user_id,
        actor_org_id=org.id,
        grant_scope="workspace",
        workspace_id=ws.id,
        user_id=None,
    )

    assert result.authorize_url.startswith("https://oauth-e2e.example.com/authorize?")
    assert "client_id=test-client-id" in result.authorize_url


async def test_start_oauth_flow_uses_resource_metadata_scopes_when_as_omits_scopes(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
) -> None:
    import secrets

    from cubebox.repositories import OrganizationRepository, WorkspaceRepository

    suffix = secrets.token_hex(4)
    org = await OrganizationRepository(db_session).create(
        name=f"Atlassian Org {suffix}",
        slug=f"atlassian-org-{suffix}",
    )
    ws = await WorkspaceRepository(db_session).create(
        org_id=org.id,
        name=f"Atlassian WS {suffix}",
    )
    template = await MCPConnectorTemplateRepository(db_session).upsert_by_slug(
        slug=f"atlassian-test-{suffix}",
        name="Atlassian Test",
        description="Atlassian Rovo MCP test connector",
        provider="Atlassian",
        server_url="https://mcp.atlassian.com/v1/mcp/authv2",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
    )
    await db_session.commit()

    user_id = "usr-atlassian-actor"
    install = await _seed_oauth_install(
        db_session,
        org_id=org.id,
        workspace_id=ws.id,
        template_id=template.id,
        install_scope="workspace",
        default_credential_policy="workspace",
        created_by_user_id=None,
    )
    service = OAuthStartService(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=_StubResourceScopesDiscovery(),  # type: ignore[arg-type]
        dcr=DCRClient(http_client),
        http_client=http_client,
    )

    result = await service.start_oauth_flow(
        connector_id=install.id,
        actor_user_id=user_id,
        actor_org_id=org.id,
        grant_scope="workspace",
        workspace_id=ws.id,
        user_id=None,
    )

    params = parse_qs(urlparse(result.authorize_url).query)
    assert params["scope"] == ["read:me search:confluence"]
    assert params["resource"] == ["https://oauth-e2e.example.com/mcp"]


# ---------------------------------------------------------------------------
# Task 2: callback handler fixtures and tests.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_oauth_org_install(
    db_session_maker: async_sessionmaker[AsyncSession],
    seed_org_workspace_user: tuple[str, str, str],
) -> tuple[str, str, str, str]:
    """Yield ``(install_id, connector_id, org_id, workspace_id)`` for an org-policy OAuth install."""
    org_id, ws_id, user_id = seed_org_workspace_user
    async with db_session_maker() as session:
        install = await _seed_oauth_install(
            session,
            org_id=org_id,
            workspace_id=None,
            default_credential_policy="org",
            created_by_user_id=user_id,
        )
        connector_id = await _connector_id_for_install(session, install)
        return install.id, connector_id, org_id, ws_id


@pytest_asyncio.fixture
async def oauth_callback_handler(
    db_session: AsyncSession,
    encryption_backend: FernetBackend,
    oauth_state_store: OAuthStateStore,
    http_client: httpx.AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> OAuthCallbackHandler:
    # Post-grant discovery would otherwise probe the fake AS host on
    # success. These tests assert the grant/install state shape, not the
    # network round-trip — short-circuit the discovery hop.
    async def _noop(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("cubebox.services.mcp_discovery.run_post_grant_discovery", _noop)

    metadata: Any = _StubDiscovery()
    from cubebox.mcp.dependencies import build_user_token_signer

    signer = build_user_token_signer()
    return OAuthCallbackHandler(
        session=db_session,
        backend=encryption_backend,
        state_store=oauth_state_store,
        metadata=metadata,
        http_client=http_client,
        signer=signer,
        redis=fake_redis,
    )


@pytest_asyncio.fixture
async def install_repo(
    db_session: AsyncSession,
    seed_org_workspace_user: tuple[str, str, str],
) -> MCPConnectorRepository:
    org_id, _ws_id, _user_id = seed_org_workspace_user
    return MCPConnectorRepository(db_session, org_id=org_id)


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
    install_repo: MCPConnectorRepository,
    seeded_oauth_install: tuple[str, str, str, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-policy install: grant lands at scope='user', auth_status STAYS
    'pending' (per spec §6 — auth_status is per-install, not per-user)."""
    install_id, connector_id, scope, ws_id, user_id, org_id = seeded_oauth_install
    assert scope == "user"

    start = await oauth_start_service.start_oauth_flow(
        connector_id=install_id,
        actor_user_id=user_id,
        actor_org_id=org_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )

    async def fake_post_token(
        _self: OAuthCallbackHandler,
        _install: MCPConnector,
        _code: str,
        _verifier: str,
        _cred_service: Any,
        _frontend_origin: str | None = None,
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
    assert result.connector_id == install_id
    assert result.state == start.state

    grant = await grant_repo.get_user_grant_for_connector(
        connector_id,
        user_id,
        workspace_id=ws_id,
    )
    assert grant is not None
    assert grant.connector_id == connector_id
    assert grant.grant_status == "valid"
    # User-policy: the connector row is not flipped to "authorized" because
    # auth_status was removed from MCPConnector in the template-centric model.
    # Verify the connector itself is still findable and intact.
    install = await install_repo.get(install_id)
    assert install is not None


async def test_callback_writes_org_grant_and_authorizes_install(
    oauth_callback_handler: OAuthCallbackHandler,
    oauth_start_service: OAuthStartService,
    grant_repo: MCPCredentialGrantRepository,
    install_repo: MCPConnectorRepository,
    seeded_oauth_org_install: tuple[str, str, str, str],
    seed_org_workspace_user: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-policy install: grant lands at scope='org', auth_status flips
    'pending' → 'authorized' because rule §6 fires."""
    install_id, connector_id, org_id, _ws_id = seeded_oauth_org_install
    _seed_org_id, _seed_ws_id, actor_user_id = seed_org_workspace_user

    start = await oauth_start_service.start_oauth_flow(
        connector_id=install_id,
        actor_user_id=actor_user_id,
        actor_org_id=org_id,
        grant_scope="org",
        workspace_id=None,
        user_id=None,
    )

    async def fake_post_token(
        _self: OAuthCallbackHandler,
        _install: MCPConnector,
        _code: str,
        _verifier: str,
        _cred_service: Any,
        _frontend_origin: str | None = None,
    ) -> dict[str, Any]:
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    monkeypatch.setattr(OAuthCallbackHandler, "_post_token_exchange", fake_post_token)

    result = await oauth_callback_handler.handle_callback(
        state=start.state,
        code="fake-code",
    )
    assert result.status == "ok"

    grant = await grant_repo.get_org_grant_for_connector(connector_id)
    assert grant is not None
    assert grant.connector_id == connector_id
    assert grant.grant_status == "valid"
    # auth_status was removed from MCPConnector in the template-centric model.
    # The grant's grant_status == "valid" is the canonical "authorized" signal now.
    install = await install_repo.get(install_id)
    assert install is not None


# Task 4 (GET /installs/{id}/effective) was removed in Task 9 template-centric
# cutover. The "effective" endpoint no longer exists. The equivalent check is
# now done via GET /admin/mcp/catalog which returns per-template catalog rows
# including org_grant_status and needs_attention fields.
