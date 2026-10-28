"""HTTP MCP client used by discovery, tools_cache, and Try It.

cubeloop's session opener is still private (``_open_client``). This module
is the only cubeplex import of that symbol so a rename stays one-file.
The yielded client is cubeloop's MCP SDK 2 ``Client`` in auto mode: it
probes ``server/discover`` (2026-07-28) and falls back to ``initialize``
for 1.x servers. Callers must not invoke ``initialize()`` themselves.
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
    headers: dict[str, str] | None,
    timeout: float,
    transport: MCPTransport,
) -> AsyncIterator[Any]:
    """Open a negotiated MCP HTTP client over ``sse`` or ``streamable_http``."""
    from cubeloop.mcp.http_loader import _open_client

    async with _open_client(
        server_url, headers=headers, timeout=timeout, transport=transport
    ) as client:
        yield client


def serialize_mcp_call_tool_response(resp: Any) -> dict[str, Any]:
    """Normalize an MCP SDK CallToolResult (v1 or v2) to the adapter dict."""
    from cubeloop.mcp._adapter import serialize_mcp_call_tool_response as _serialize

    return _serialize(resp)
