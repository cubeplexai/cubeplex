"""Admin-side MCP discovery (list-tools only) for cubepi runtime.

Replaces the langchain-mcp-adapters MultiServerMCPClient path in the old
cubebox.mcp.discovery module. Uses the raw `mcp` SDK to call
`session.list_tools()` and serialize the result to the same
``{name, description, input_schema}`` shape persisted in
``MCPServer.tools_cache``.

The cubepi per-run path uses ``cubepi.mcp.load_mcp_tools_http`` directly
and does NOT consult this cache; tools_cache is admin-UI metadata.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from mcp import ClientSession
from mcp.client.sse import sse_client

from cubebox.mcp.connection_params import build_connection_params
from cubebox.models import MCPServer

_TIMEOUT = 30.0


async def discover_tools_metadata(
    server: MCPServer,
    *,
    credential_or_token: str | None,
) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    """Connect, list tools, return (success, serialized tools | None, error | None).

    Same return contract as the deprecated ``cubebox.mcp.discovery.discover_tools``.
    """
    try:
        params = build_connection_params(server, credential_or_token=credential_or_token)
    except ValueError as exc:
        return False, None, f"params build failed: {exc}"

    url = params.get("url")
    headers = params.get("headers", {})
    if not isinstance(url, str) or not url:
        return False, None, "missing or invalid url in connection params"

    try:
        async with sse_client(
            url, headers=headers, timeout=_TIMEOUT, sse_read_timeout=_TIMEOUT
        ) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                resp = await session.list_tools()
    except BaseExceptionGroup as exc:
        causes = "; ".join(str(sub) for sub in exc.exceptions)
        return False, None, f"{exc}; causes: {causes}"
    except Exception as exc:
        return False, None, str(exc)

    tools: list[dict[str, Any]] = []
    for desc in resp.tools or []:
        tools.append(
            {
                "name": desc.name,
                "description": desc.description or "",
                "input_schema": desc.inputSchema or {"type": "object", "properties": {}},
            }
        )

    logger.debug("MCP discovery: server={}, tools={}", server.name, len(tools))
    return True, tools, None
