"""Build MCP client connection params from a DB MCPServer and resolved credential."""

from typing import Any

from cubebox.models import MCPServer

_HTTP_TRANSPORTS = {"streamable_http", "sse"}


def build_connection_params(
    server: MCPServer,
    *,
    credential_or_token: str | None,
) -> dict[str, Any]:
    """Build MultiServerMCPClient params dict for one server."""
    if server.transport in _HTTP_TRANSPORTS:
        return _http_params(server, credential_or_token)
    if server.transport == "stdio":
        return _stdio_params(server, credential_or_token)
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


def _stdio_params(server: MCPServer, token: str | None) -> dict[str, Any]:
    """Use server_url as '<command> [args...]'; token may be injected via env."""
    command_parts = server.server_url.split()
    if not command_parts:
        raise ValueError("stdio server_url must contain command")

    params: dict[str, Any] = {
        "command": command_parts[0],
        "args": command_parts[1:],
        "transport": "stdio",
    }
    env_var = server.headers.get("env_var_for_token") if server.headers else None
    if token and env_var:
        params["env"] = {env_var: token}
    return params
