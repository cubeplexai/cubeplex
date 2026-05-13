"""MCP server discovery for the cubepi runtime (M2.4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
)
from cubebox.services.credential import CredentialService

logger = logging.getLogger(__name__)


@dataclass
class CubepiMCPServerSpec:
    """Resolved MCP server ready for cubepi.mcp.load_mcp_tools_http."""

    server_id: str
    server_name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


async def discover_workspace_mcp_servers_for_cubepi(
    *,
    session: AsyncSession,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
) -> list[CubepiMCPServerSpec]:
    """Resolve workspace-enabled MCP servers + decrypt credentials → CubepiMCPServerSpec list.

    Re-uses the existing DB resolution logic (MCPServerRepository.list_for_workspace) and
    credential decryption from CredentialService. Servers with missing or unresolvable
    credentials are skipped with a warning rather than raising.

    Only HTTP/SSE transports are supported (stdio was removed; this mirrors cubebox's current
    production stance). Servers requiring a user credential (scope='user') without a resolved
    token are excluded — same semantics as the langchain runtime.
    """
    server_repo = MCPServerRepository(session, org_id=org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=org_id)

    specs: list[CubepiMCPServerSpec] = []
    for server in await server_repo.list_for_workspace(workspace_id):
        try:
            token = await _resolve_token_for_cubepi(
                server_id=server.id,
                server_name=server.name,
                auth_method=server.auth_method,
                credential_scope=server.credential_scope,
                credential_id=server.credential_id,
                workspace_id=workspace_id,
                user_id=user_id,
                cred_service=cred_service,
                ws_cred_repo=ws_cred_repo,
                user_cred_repo=user_cred_repo,
            )
        except CredentialNotFound:
            logger.warning(
                "MCP server '%s' references a missing credential; skipping",
                server.name,
            )
            continue
        except Exception as exc:
            logger.warning(
                "MCP server '%s' credential resolution failed: %s; skipping",
                server.name,
                exc,
            )
            continue

        # Servers that require a credential but have none resolved are excluded.
        if token is None and server.credential_scope != "none":
            continue

        # Build the Authorization header if we have a token.
        headers: dict[str, str] = dict(server.headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"

        specs.append(
            CubepiMCPServerSpec(
                server_id=server.id,
                server_name=server.name,
                url=server.server_url,
                headers=headers,
            )
        )

    return specs


async def _resolve_token_for_cubepi(
    *,
    server_id: str,
    server_name: str,
    auth_method: str,
    credential_scope: str,
    credential_id: str | None,
    workspace_id: str,
    user_id: str,
    cred_service: CredentialService,
    ws_cred_repo: WorkspaceMCPCredentialRepository,
    user_cred_repo: UserMCPCredentialRepository,
) -> str | None:
    """Resolve the bearer token/credential for one MCP server.

    Mirrors the logic in runtime._resolve_token but without OAuth token-manager
    support (which is a runtime concern, not a discovery concern) and without
    MCPUserTokenSigner (not needed for the cubepi path since cubepi manages its
    own session model).
    """
    cred_kind = (
        CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN if auth_method == "oauth" else CREDENTIAL_KIND_MCP
    )

    if credential_scope == "org":
        if credential_id is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=credential_id,
            requesting_kind=cred_kind,
        )

    if credential_scope == "workspace":
        workspace_credential = await ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if workspace_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=workspace_credential.credential_id,
            requesting_kind=cred_kind,
        )

    if credential_scope == "user":
        user_credential = await user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server_id,
        )
        if user_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=user_credential.credential_id,
            requesting_kind=cred_kind,
        )

    # credential_scope == "none": no bearer token needed.
    return None
