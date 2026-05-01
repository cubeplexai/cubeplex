"""Per-(workspace, user) DB MCP tool assembly for agent runs."""

from datetime import timedelta

from langchain_core.tools import BaseTool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp.connection_params import build_connection_params
from cubebox.mcp.discovery import construct_basetools_from_cache
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models import MCPServer
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
)
from cubebox.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)
_CREDENTIAL_KIND_MCP = "mcp_server"


async def load_mcp_tools_for_workspace(
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    session: AsyncSession,
) -> list[BaseTool]:
    """Resolve visible DB MCP servers and build run-scoped LangChain tools."""
    server_repo = MCPServerRepository(session, org_id=org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=org_id)

    tools: list[BaseTool] = []
    for server in await server_repo.list_for_workspace(workspace_id):
        try:
            token = await _resolve_token(
                server,
                user_id=user_id,
                workspace_id=workspace_id,
                cred_service=cred_service,
                signer=signer,
                ws_cred_repo=ws_cred_repo,
                user_cred_repo=user_cred_repo,
            )
        except CredentialNotFound:
            logger.warning(
                "MCP server '{}' references a missing credential; skipping",
                server.name,
            )
            continue
        except Exception as exc:
            logger.warning(
                "MCP server '{}' credential resolution failed: {}; skipping",
                server.name,
                exc,
            )
            continue

        if token is None and server.credential_scope != "none":
            continue

        try:
            connection_params = build_connection_params(server, credential_or_token=token)
            tools.extend(construct_basetools_from_cache(server.tools_cache, connection_params))
        except Exception as exc:
            logger.warning(
                "MCP server '{}' tool construction failed: {}; skipping",
                server.name,
                exc,
            )

    return tools


async def _resolve_token(
    server: MCPServer,
    *,
    user_id: str,
    workspace_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    ws_cred_repo: WorkspaceMCPCredentialRepository,
    user_cred_repo: UserMCPCredentialRepository,
) -> str | None:
    if server.credential_scope == "org":
        if server.credential_id is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=server.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )

    if server.credential_scope == "workspace":
        workspace_credential = await ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server.id,
        )
        if workspace_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=workspace_credential.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )

    if server.credential_scope == "user":
        user_credential = await user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server.id,
        )
        if user_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=user_credential.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )

    if server.credential_scope == "none":
        return await signer.sign(
            user_id=user_id,
            org_id=server.org_id,
            workspace_id=workspace_id,
            mcp_server_id=server.id,
            ttl=_USER_TOKEN_TTL,
        )

    return None
