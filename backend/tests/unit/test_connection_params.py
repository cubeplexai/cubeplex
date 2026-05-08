"""Unit tests for MCP connection params dispatch."""

from typing import Any

import pytest

from cubebox.mcp.connection_params import build_connection_params
from cubebox.models import MCPServer


def _server(**overrides: object) -> MCPServer:
    base: dict[str, Any] = {
        "org_id": "o",
        "name": "t",
        "server_url": "https://x",
        "server_url_hash": "h",
        "transport": "streamable_http",
        "auth_method": "static",
        "credential_scope": "org",
        "credential_id": "c",
        "created_by_user_id": "u",
    }
    base.update(overrides)
    return MCPServer(**base)


def test_streamable_http_with_static_token() -> None:
    server = _server(transport="streamable_http", credential_scope="org")

    params = build_connection_params(server, credential_or_token="ghp_xxx")

    assert params["url"] == "https://x"
    assert params["transport"] == "streamable_http"
    assert params["headers"] == {"Authorization": "Bearer ghp_xxx"}


def test_sse_with_static_token() -> None:
    server = _server(transport="sse")

    params = build_connection_params(server, credential_or_token="tok")

    assert params["transport"] == "sse"
    assert params["headers"]["Authorization"] == "Bearer tok"


def test_stdio_transport_rejected() -> None:
    server = _server(transport="stdio")

    with pytest.raises(ValueError, match="unsupported transport"):
        build_connection_params(server, credential_or_token="x")


def test_none_scope_no_auth_header() -> None:
    server = _server(
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
        credential_id=None,
    )

    params = build_connection_params(server, credential_or_token=None)

    assert "Authorization" not in params.get("headers", {})


def test_user_passthrough_uses_jwt_in_header() -> None:
    server = _server(credential_scope="user", credential_id=None)

    params = build_connection_params(server, credential_or_token="<jwt-token>")

    assert params["headers"]["Authorization"] == "Bearer <jwt-token>"


def test_custom_headers_merged() -> None:
    server = _server(headers={"X-Custom": "v"})

    params = build_connection_params(server, credential_or_token="tok")

    assert params["headers"]["X-Custom"] == "v"
    assert params["headers"]["Authorization"] == "Bearer tok"


def test_unknown_transport_raises() -> None:
    server = _server(transport="something_weird")

    with pytest.raises(ValueError, match="unsupported transport"):
        build_connection_params(server, credential_or_token="x")
