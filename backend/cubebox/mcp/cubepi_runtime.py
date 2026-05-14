"""MCP tool loading for the cubepi runtime (M2.4)."""

from __future__ import annotations

import logging
from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp.cubepi_discovery import discover_workspace_mcp_servers_for_cubepi
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.services.credential import CredentialService

logger = logging.getLogger(__name__)


async def load_workspace_mcp_tools_for_cubepi(
    *,
    session: AsyncSession,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
) -> list[AgentTool[Any]]:
    """Load all enabled MCP servers' tools for a workspace as cubepi.AgentTool.

    Per-server failures are caught and logged, never aborting the load.

    Only HTTP/SSE transports are supported — stdio was dropped; this aligns with
    cubebox's current production stance and cubepi.mcp.load_mcp_tools_http scope.
    """
    servers = await discover_workspace_mcp_servers_for_cubepi(
        session=session,
        workspace_id=workspace_id,
        org_id=org_id,
        user_id=user_id,
        cred_service=cred_service,
        signer=signer,
    )

    all_tools: list[AgentTool[Any]] = []
    for spec in servers:
        try:
            tools = await load_mcp_tools_http(
                spec.url,
                headers=spec.headers or None,
                timeout=30.0,
            )
            all_tools.extend(tools)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load MCP server %s (%s): %s",
                spec.server_name,
                spec.server_id,
                exc,
            )
    return all_tools
