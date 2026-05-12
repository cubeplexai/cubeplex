"""Per-(workspace, user) DB MCP tool assembly for agent runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.tools import BaseTool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
from cubebox.mcp.connection_params import build_connection_params
from cubebox.mcp.discovery import construct_basetools_from_cache, discover_tools
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models import MCPServer
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)


async def refresh_tools_for_server_with_token(
    server: MCPServer,
    *,
    server_repo: MCPServerRepository,
    credential_or_token: str | None,
) -> None:
    """Run tool discovery against ``server`` and persist the result.

    Mirrors ``MCPServerService._refresh_tools_for_server_with_token`` so the
    OAuth callback handler can re-run discovery without instantiating a
    request-scoped service. Updates ``authed`` / ``tools_cache`` / ``last_error``
    / ``last_discovered_at`` and commits via the repository.
    """
    success, tools, error = await discover_tools(server, credential_or_token=credential_or_token)
    server.authed = success
    server.tools_cache = tools or []
    server.last_error = None if success else error
    server.last_discovered_at = datetime.now(UTC)
    await server_repo.update(server)


async def load_mcp_tools_for_workspace(
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    session: AsyncSession,
    token_manager: OAuthTokenManager | None = None,
) -> list[BaseTool]:
    """Resolve visible DB MCP servers and build run-scoped LangChain tools."""
    server_repo = MCPServerRepository(session, org_id=org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=org_id)
    override_repo = WorkspaceMCPOverrideRepository(session, org_id=org_id)

    tools: list[BaseTool] = []
    for server in await server_repo.list_for_workspace(workspace_id):
        try:
            effective_mode = await _effective_credential_mode(
                server=server,
                workspace_id=workspace_id,
                override_repo=override_repo,
            )
            token = await _resolve_token(
                server,
                effective_mode=effective_mode,
                user_id=user_id,
                workspace_id=workspace_id,
                cred_service=cred_service,
                signer=signer,
                ws_cred_repo=ws_cred_repo,
                user_cred_repo=user_cred_repo,
                token_manager=token_manager,
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

        if token is None and effective_mode != "none":
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


async def _effective_credential_mode(
    *,
    server: MCPServer,
    workspace_id: str,
    override_repo: WorkspaceMCPOverrideRepository,
) -> str:
    """Per-workspace effective credential mode for an MCP server.

    Mirrors ``MCPServerService._effective_credential_mode``: a workspace
    override may declare ``credential_mode`` that takes precedence over the
    server-level ``credential_scope`` default. Workspace-owned servers always
    fall back to ``credential_scope``.
    """
    if server.owner_workspace_id is not None:
        return server.credential_scope
    override = await override_repo.get_for_workspace_and_server(
        workspace_id=workspace_id,
        mcp_server_id=server.id,
    )
    if override is None or not override.enabled or not override.credential_mode:
        return server.credential_scope
    return override.credential_mode


async def _resolve_token(
    server: MCPServer,
    *,
    effective_mode: str,
    user_id: str,
    workspace_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    ws_cred_repo: WorkspaceMCPCredentialRepository,
    user_cred_repo: UserMCPCredentialRepository,
    token_manager: OAuthTokenManager | None = None,
) -> str | None:
    # OAuth installs: delegate to OAuthTokenManager which handles expiry
    # checks and automatic refresh_token grants.
    if server.auth_method == "oauth" and token_manager is not None:
        return await token_manager.get_valid_access_token(
            server, user_id=user_id if effective_mode == "user" else None
        )

    cred_kind = (
        CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
        if server.auth_method == "oauth"
        else CREDENTIAL_KIND_MCP
    )

    if effective_mode == "org":
        if server.credential_id is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=server.credential_id,
            requesting_kind=cred_kind,
        )

    if effective_mode == "workspace":
        workspace_credential = await ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server.id,
        )
        if workspace_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=workspace_credential.credential_id,
            requesting_kind=cred_kind,
        )

    if effective_mode == "user":
        user_credential = await user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server.id,
        )
        if user_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=user_credential.credential_id,
            requesting_kind=cred_kind,
        )

    if effective_mode == "none":
        return await signer.sign(
            user_id=user_id,
            org_id=server.org_id,
            workspace_id=workspace_id,
            mcp_server_id=server.id,
            ttl=_USER_TOKEN_TTL,
        )

    return None
