"""FastAPI DI providers for DB-backed MCP services."""

from typing import cast

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member, require_org_admin, resolve_current_org_id
from cubebox.config import config
from cubebox.credentials.dependencies import (
    build_credential_service,
    get_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.mcp.oauth.callback import CredentialServiceFactory, OAuthCallbackHandler
from cubebox.mcp.oauth.dcr import DCRClient
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.start import OAuthStartService
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.mcp.user_token import HS256Signer, MCPUserTokenSigner
from cubebox.models import Role, User
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService
from cubebox.services.mcp import MCPServerService
from cubebox.services.mcp_catalog import MCPCatalogService


def build_user_token_signer() -> MCPUserTokenSigner:
    secret = config.get("auth.jwt_secret")
    if not secret:
        raise RuntimeError("CUBEBOX_AUTH__JWT_SECRET missing")
    return HS256Signer(secret=str(secret))


async def get_user_token_signer(request: Request) -> MCPUserTokenSigner:
    return cast(MCPUserTokenSigner, request.app.state.mcp_user_token_signer)


async def get_audit_sink(request: Request) -> AuditSink:
    return cast(AuditSink, request.app.state.audit_sink)


async def get_mcp_service(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPServerService:
    return MCPServerService(
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ctx.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        request_context=ctx,
    )


async def get_admin_request_context(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_org_admin),
) -> RequestContext:
    org_id = await resolve_current_org_id(user, session)
    return RequestContext(user=user, org_id=org_id, workspace_id="", role=Role.ADMIN)


async def get_admin_mcp_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> MCPServerService:
    cred_service = build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return MCPServerService(
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ctx.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        request_context=ctx,
    )


async def get_member_catalog_service(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPCatalogService:
    """Catalog service for member-scoped reads and workspace user installs."""
    return MCPCatalogService(
        catalog_repo=MCPCatalogConnectorRepository(session),
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ctx.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        request_context=ctx,
    )


async def get_admin_catalog_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> MCPCatalogService:
    """Catalog service for org admin install/delete/switch-auth flows."""
    cred_service = build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return MCPCatalogService(
        catalog_repo=MCPCatalogConnectorRepository(session),
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ctx.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        request_context=ctx,
    )


# ---------------- OAuth wiring ---------------- #


async def get_redis(request: Request) -> Redis:
    """Return the shared async Redis client established at lifespan."""
    return cast(Redis, request.app.state.redis)


def _oauth_redirect_uri() -> str:
    """The fixed callback URI minted from ``public_base_url``.

    Per spec §9 the callback path is fixed. We honor public_base_url
    so OAuth deployments behind a reverse proxy advertise the
    externally-reachable URL to the AS rather than the bind address.
    """
    base = str(config.get("public_base_url", "http://localhost:8000")).rstrip("/")
    return f"{base}/api/v1/oauth/mcp/callback"


def _state_secret_key() -> bytes:
    secret = config.get("auth.csrf_secret")
    if not secret:
        raise RuntimeError("CUBEBOX_AUTH__CSRF_SECRET missing")
    return str(secret).encode("utf-8")


_HTTP_CLIENT_KEY = "_mcp_oauth_http_client"
_OAUTH_METADATA_DISCOVERY_KEY = "_mcp_oauth_metadata_discovery"


async def get_oauth_http_client(request: Request) -> httpx.AsyncClient:
    """Lazy-initialize a shared ``httpx.AsyncClient`` for OAuth IO.

    We don't open a global pool from app lifespan — only routes that
    actually need it pay for the connection. The client is cached on
    ``app.state`` so subsequent requests reuse a single pool.
    """
    client = getattr(request.app.state, _HTTP_CLIENT_KEY, None)
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        setattr(request.app.state, _HTTP_CLIENT_KEY, client)
    return cast(httpx.AsyncClient, client)


async def get_oauth_state_store(
    redis: Redis = Depends(get_redis),
) -> OAuthStateStore:
    return OAuthStateStore(redis=redis, secret_key=_state_secret_key())


async def get_oauth_metadata_discovery(
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> OAuthMetadataDiscovery:
    """Return the app-lifetime ``OAuthMetadataDiscovery`` instance.

    The discovery client carries an in-memory TTL cache for AS / PR
    well-known documents. Constructing a new instance per request would
    reset that cache on every call, defeating the cache entirely. We
    stash a single instance on ``app.state`` (mirroring the
    ``_HTTP_CLIENT_KEY`` pattern above) so the cache survives across
    requests for the lifetime of the process.
    """
    cached = getattr(request.app.state, _OAUTH_METADATA_DISCOVERY_KEY, None)
    if cached is None:
        cached = OAuthMetadataDiscovery(http_client)
        setattr(request.app.state, _OAUTH_METADATA_DISCOVERY_KEY, cached)
    return cast(OAuthMetadataDiscovery, cached)


async def get_oauth_dcr_client(
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> DCRClient:
    return DCRClient(http_client)


def _credential_service_factory_for_session(
    session: AsyncSession,
    backend: EncryptionBackend,
) -> CredentialServiceFactory:
    """Build a ``(org_id, actor_user_id) -> CredentialService`` factory bound to ``session``."""

    def factory(org_id: str | None, actor_user_id: str | None) -> CredentialService:
        return build_credential_service(
            session,
            backend,
            org_id=org_id,
            actor_user_id=actor_user_id,
        )

    return factory


async def get_oauth_start_service_admin(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    redis: Redis = Depends(get_redis),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    dcr_client: DCRClient = Depends(get_oauth_dcr_client),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> OAuthStartService:
    cred_service = build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return OAuthStartService(
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        catalog_repo=MCPCatalogConnectorRepository(session),
        metadata=metadata,
        dcr_client=dcr_client,
        state_store=state_store,
        credential_service=cred_service,
        redis=redis,
        redirect_uri=_oauth_redirect_uri(),
        org_id=ctx.org_id,
    )


async def get_oauth_start_service_member(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    redis: Redis = Depends(get_redis),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    dcr_client: DCRClient = Depends(get_oauth_dcr_client),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    ctx: RequestContext = Depends(require_member),
) -> OAuthStartService:
    return OAuthStartService(
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        catalog_repo=MCPCatalogConnectorRepository(session),
        metadata=metadata,
        dcr_client=dcr_client,
        state_store=state_store,
        credential_service=cred_service,
        redis=redis,
        redirect_uri=_oauth_redirect_uri(),
        org_id=ctx.org_id,
    )


async def get_oauth_callback_handler(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    redis: Redis = Depends(get_redis),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> OAuthCallbackHandler:
    """Callback handler runs on an unauthenticated GET — no RequestContext.

    Repos use ``org_id=None`` because the callback derives the org from
    the install referenced in the (HMAC-verified) state token.
    """
    return OAuthCallbackHandler(
        http_client=http_client,
        redis=redis,
        state_store=state_store,
        metadata=metadata,
        encryption_backend=backend,
        credential_service_factory=_credential_service_factory_for_session(session, backend),
        server_repo=MCPServerRepository(session, org_id=None),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=None),
        redirect_uri=_oauth_redirect_uri(),
    )
