"""HTTP MCP client used by discovery, tools_cache, and Try It.

Thin wrapper around cubeloop's public opener and serializer so a
cubeloop rename stays one-file. The yielded client is cubeloop's MCP
SDK 2 ``Client`` in auto mode: it probes ``server/discover``
(2026-07-28) and falls back to ``initialize`` for 1.x servers.
Callers must not invoke ``initialize()`` themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

MCPTransport = Literal["sse", "streamable_http"]


@asynccontextmanager
async def open_mcp_http_client(
    server_url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    transport: MCPTransport = "sse",
) -> AsyncIterator[Any]:
    """Open a negotiated MCP HTTP client over ``sse`` or ``streamable_http``."""
    from cubeloop.mcp import open_mcp_http_client as cubeloop_open_mcp_http_client

    async with cubeloop_open_mcp_http_client(
        server_url, headers=headers, timeout=timeout, transport=transport
    ) as client:
        yield client


def serialize_mcp_call_tool_response(resp: Any) -> dict[str, Any]:
    """Normalize an MCP SDK CallToolResult (v1 or v2) to the adapter dict."""
    from cubeloop.mcp import serialize_mcp_call_tool_response as _serialize

    return _serialize(resp)
