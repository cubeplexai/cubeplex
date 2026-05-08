"""Build MCP client connection params from a DB MCPServer and resolved credential."""

from typing import Any

from cubebox.models import MCPServer

_HTTP_TRANSPORTS = {"streamable_http", "sse"}


def build_connection_params(
    server: MCPServer,
    *,
    credential_or_token: str | None,
) -> dict[str, Any]:
    """Build MultiServerMCPClient params dict for one server.

    Only HTTP-flavoured transports are supported; stdio was removed in M2 (the
    server side never invokes third-party processes).
    """
    if server.transport in _HTTP_TRANSPORTS:
        return _http_params(server, credential_or_token)
    raise ValueError(f"unsupported transport '{server.transport}'")


def _http_params(server: MCPServer, token: str | None) -> dict[str, Any]:
    headers = dict(server.headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params: dict[str, Any] = {
        "url": server.server_url,
        "transport": server.transport,
    }
    if headers:
        params["headers"] = headers
    return params
