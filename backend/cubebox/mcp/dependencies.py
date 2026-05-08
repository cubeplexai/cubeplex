"""FastAPI DI providers for DB-backed MCP services."""

from typing import cast

from fastapi import Depends, Request
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
from cubebox.mcp.user_token import HS256Signer, MCPUserTokenSigner
from cubebox.models import Role, User
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.services.credential import CredentialService
from cubebox.services.mcp import MCPServerService


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
