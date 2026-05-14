"""Admin-side MCP discovery (list-tools only) for cubepi runtime.

Replaces the langchain-mcp-adapters MultiServerMCPClient path in the old
cubebox.mcp.discovery module. Uses the raw `mcp` SDK to call
`session.list_tools()` and serialize the result to the same
``{name, description, input_schema}`` shape persisted in
``MCPServer.tools_cache``.

Dispatches by ``MCPServer.transport``: ``sse`` opens an SSE stream,
``streamable_http`` opens a streamable-HTTP connection. The cubepi per-run
path (``cubepi.mcp.load_mcp_tools_http``) is currently SSE-only; matching
both transports here keeps the admin UI's tool listing correct for
streamable_http servers even before the per-run path is updated upstream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from cubebox.mcp.connection_params import build_connection_params
from cubebox.models import MCPServer

_TIMEOUT = 30.0


@asynccontextmanager
async def _open_session(
    *,
    transport: str,
    url: str,
    headers: dict[str, str],
) -> AsyncIterator[ClientSession]:
    """Open an MCP ClientSession over the requested transport.

    Normalises the two SDK transport client signatures (sse_client yields
    a 2-tuple; streamablehttp_client yields a 3-tuple with a session-id
    callable) so the caller can use a single ``ClientSession`` block.
    """
    if transport == "streamable_http":
        async with streamablehttp_client(
            url, headers=headers, timeout=_TIMEOUT, sse_read_timeout=_TIMEOUT
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                yield session
        return

    if transport == "sse":
        async with sse_client(
            url, headers=headers, timeout=_TIMEOUT, sse_read_timeout=_TIMEOUT
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                yield session
        return

    raise ValueError(f"unsupported transport '{transport}'")


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
    transport = params.get("transport")
    headers = params.get("headers", {})
    if not isinstance(url, str) or not url:
        return False, None, "missing or invalid url in connection params"
    if not isinstance(transport, str) or not transport:
        return False, None, "missing or invalid transport in connection params"

    try:
        async with _open_session(transport=transport, url=url, headers=headers) as session:
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

    logger.debug(
        "MCP discovery: server={}, transport={}, tools={}", server.name, transport, len(tools)
    )
    return True, tools, None
