"""MCP tool loading for the cubepi runtime (M2.4)."""

from __future__ import annotations

import logging
from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp.cubepi_discovery import discover_workspace_mcp_servers_for_cubepi
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.middleware.citations.config import CitationConfig
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
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Load all enabled MCP servers' tools for a workspace as cubepi.AgentTool.

    Tool names are namespaced as ``{server_name}__{tool_name}`` so two MCP
    servers can ship the same bare tool name without colliding in the
    agent's tool list. The returned ``CitationConfig`` dict uses the same
    namespaced keys.

    Per-server failures are caught and logged, never aborting the load.
    Only HTTP/SSE transports are supported.
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
    all_citations: dict[str, CitationConfig] = {}
    for spec in servers:
        try:
            tools = await load_mcp_tools_http(
                spec.url,
                headers=spec.headers or None,
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load MCP server %s (%s): %s",
                spec.server_name,
                spec.server_id,
                exc,
            )
            continue

        prefix = f"{spec.server_name}__"
        for tool in tools:
            bare_name = tool.name
            tool.name = f"{prefix}{bare_name}"
            all_tools.append(tool)
            raw = (spec.tool_citations or {}).get(bare_name)
            if raw is None:
                continue
            try:
                all_citations[tool.name] = CitationConfig(**raw)
            except ValidationError as exc:
                logger.warning(
                    "Bad tool_citations on %s/%s: %s — skipping",
                    spec.server_name,
                    bare_name,
                    exc,
                )

    return all_tools, all_citations
