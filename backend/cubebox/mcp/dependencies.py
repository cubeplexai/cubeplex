"""FastAPI DI providers for DB-backed MCP services (four-layer only)."""

from typing import cast

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import (
    request_context,
    require_org_admin,
    resolve_current_org_id,
)
from cubebox.config import config
from cubebox.credentials.dependencies import (
    build_credential_service,
    get_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.mcp.oauth import (
    DCRClient,
    OAuthCallbackHandler,
    OAuthMetadataDiscovery,
    OAuthStartService,
    OAuthStateStore,
)
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import HS256Signer, MCPUserTokenSigner
from cubebox.models import Role, User
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.repositories.workspace import WorkspaceRepository
from cubebox.services.credential import CredentialService
from cubebox.services.mcp_installs import MCPConnectorInstallService
from cubebox.services.mcp_templates import MCPConnectorTemplateService


def build_user_token_signer() -> MCPUserTokenSigner:
    secret = config.get("auth.jwt_secret")
    if not secret:
        raise RuntimeError("CUBEBOX_AUTH__JWT_SECRET missing")
    return HS256Signer(secret=str(secret))


async def get_user_token_signer(request: Request) -> MCPUserTokenSigner:
    return cast(MCPUserTokenSigner, request.app.state.mcp_user_token_signer)


async def get_audit_sink(request: Request) -> AuditSink:
    return cast(AuditSink, request.app.state.audit_sink)


# ---------------- OAuth wiring (must precede services that depend on it) ---------------- #


async def get_redis(request: Request) -> Redis:
    """Return the shared async Redis client established at lifespan."""
    return cast(Redis, request.app.state.redis)


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


async def get_admin_request_context(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_org_admin),
) -> RequestContext:
    org_id = await resolve_current_org_id(user, session)
    return RequestContext(user=user, org_id=org_id, workspace_id="", role=Role.ADMIN)


# ---------------- OAuth state store ---------------- #


def _state_secret_key() -> bytes:
    secret = config.get("auth.csrf_secret")
    if not secret:
        raise RuntimeError("CUBEBOX_AUTH__CSRF_SECRET missing")
    return str(secret).encode("utf-8")


async def get_oauth_state_store(
    redis: Redis = Depends(get_redis),
) -> OAuthStateStore:
    return OAuthStateStore(redis=redis, secret_key=_state_secret_key())


# ---------------- Four-layer (template / install / state / grant) providers ---------------- #


async def get_connector_template_service(
    session: AsyncSession = Depends(get_session),
) -> MCPConnectorTemplateService:
    """Global, no-org-scope view over the connector template catalog.

    Used by both admin (``/admin/mcp/templates``) and workspace
    (``/ws/{ws}/mcp/templates``) routes. Templates carry no ``org_id``
    so the repo and service take no scope arguments.
    """
    return MCPConnectorTemplateService(MCPConnectorTemplateRepository(session))


async def get_ws_install_service(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    ctx: RequestContext = Depends(request_context),
) -> MCPConnectorInstallService:
    """Install service bound to the caller's workspace membership org.

    Workspace routes construct the three org-scoped repos with the
    org_id resolved from membership; the actor user id is stamped on
    install / state / grant rows for audit. ``workspace_repo`` is
    wired in even though workspace routes never call the ``mode='all'``
    fan-out — keeping the wiring uniform across both providers lets
    the service code stay free of "is this admin or member" branching.
    """
    return MCPConnectorInstallService(
        install_repo=MCPConnectorInstallRepository(session, org_id=ctx.org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        workspace_repo=WorkspaceRepository(session),
        connector_repo=MCPConnectorRepository(session, org_id=ctx.org_id),
    )


async def get_ws_effective_service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(request_context),
) -> MCPEffectiveConnectorService:
    """Effective connector service for workspace ``GET /connectors``.

    Wires the four repos with the caller's org. Token manager is deferred
    here (UI surface), so we leave it as ``None``; the runtime path that
    needs OAuth refresh builds its own service in ``streams.run_manager``.
    """
    return MCPEffectiveConnectorService(
        template_repo=MCPConnectorTemplateRepository(session),
        install_repo=MCPConnectorInstallRepository(session, org_id=ctx.org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=ctx.org_id),
        org_id=ctx.org_id,
    )


async def get_admin_install_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> MCPConnectorInstallService:
    """Install service for org admin routes.

    Admin routes don't carry a ``workspace_id`` in the path, so we
    can't use ``get_credential_service`` (which depends on
    ``request_context``); we build the credential service inline with
    the admin context's ``org_id`` and ``actor_user_id``.
    """
    cred_service = build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return MCPConnectorInstallService(
        install_repo=MCPConnectorInstallRepository(session, org_id=ctx.org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        workspace_repo=WorkspaceRepository(session),
        connector_repo=MCPConnectorRepository(session, org_id=ctx.org_id),
    )


# ---------------- OAuth start / callback DI factories (Task 3) ---------------- #


async def get_dcr_client(
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> DCRClient:
    """Return a fresh DCRClient bound to the shared httpx pool."""
    return DCRClient(http_client)


async def get_oauth_start_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    dcr: DCRClient = Depends(get_dcr_client),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> OAuthStartService:
    """Build the OAuth start orchestrator.

    Credential service and install repo are deliberately built inside
    ``start_oauth_flow`` so the route surface (admin start has no workspace
    path) doesn't need ``request_context``.
    """
    return OAuthStartService(
        session=session,
        backend=backend,
        state_store=state_store,
        metadata=metadata,
        dcr=dcr,
        http_client=http_client,
    )


async def get_oauth_callback_handler(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
    signer: MCPUserTokenSigner = Depends(get_user_token_signer),
    redis: Redis = Depends(get_redis),
) -> OAuthCallbackHandler:
    """Callback handler is unauthenticated — defers org-scoped construction
    until ``handle_callback()`` decodes the state token and resolves the
    install (and hence org_id). ``signer`` and ``redis`` are passed in so
    the handler can run post-grant discovery without re-resolving them
    via DI mid-flight.
    """
    return OAuthCallbackHandler(
        session=session,
        backend=backend,
        state_store=state_store,
        metadata=metadata,
        http_client=http_client,
        signer=signer,
        redis=redis,
    )


async def get_grant_repo(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> MCPCredentialGrantRepository:
    """Org-scoped grant repo, used by admin endpoints (org-row effective)."""
    return MCPCredentialGrantRepository(session, org_id=ctx.org_id)


async def get_ws_grant_repo(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(request_context),
) -> MCPCredentialGrantRepository:
    """Org-scoped grant repo for workspace routes (org_id from membership)."""
    return MCPCredentialGrantRepository(session, org_id=ctx.org_id)


async def get_oauth_token_manager(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    redis: Redis = Depends(get_redis),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
    ctx: RequestContext = Depends(request_context),
) -> OAuthTokenManager:
    """Build an OAuth ``OAuthTokenManager`` bound to the caller's org.

    Reuses the shared httpx pool and OAuth metadata cache stashed on
    ``app.state`` by the existing DI factories. The token manager is
    request-scoped because it needs a session-bound
    ``CredentialRepository``.
    """
    from cubebox.repositories.credential import CredentialRepository

    return OAuthTokenManager(
        http_client=http_client,
        redis=redis,
        encryption_backend=backend,
        credential_repo=CredentialRepository(session, org_id=ctx.org_id),
        metadata=metadata,
    )


async def get_admin_oauth_token_manager(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    redis: Redis = Depends(get_redis),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
    ctx: RequestContext = Depends(get_admin_request_context),
) -> OAuthTokenManager:
    """Admin variant of ``get_oauth_token_manager`` — org_id comes from
    ``get_admin_request_context`` instead of workspace membership."""
    from cubebox.repositories.credential import CredentialRepository

    return OAuthTokenManager(
        http_client=http_client,
        redis=redis,
        encryption_backend=backend,
        credential_repo=CredentialRepository(session, org_id=ctx.org_id),
        metadata=metadata,
    )
