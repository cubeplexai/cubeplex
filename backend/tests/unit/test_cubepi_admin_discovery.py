"""Unit tests for cubepi_admin_discovery — list tools via raw mcp SDK."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.mcp.cubepi_admin_discovery import discover_tools_metadata
from cubebox.models import MCPServer


def _make_server(transport: str) -> MCPServer:
    return MCPServer(
        id="mcp-test1",
        org_id="org-test",
        name="test-server",
        server_url="https://mcp.example.com/sse",
        server_url_hash="hash",
        transport=transport,
        auth_method="bearer",
        credential_scope="org",
        owner_workspace_id=None,
        credential_id=None,
        authed=False,
        tools_cache=[],
        headers={},
        created_by_user_id="user-test",
    )


@pytest.fixture
def sse_server() -> MCPServer:
    return _make_server("sse")


@pytest.fixture
def streamable_http_server() -> MCPServer:
    return _make_server("streamable_http")


def _fake_session(tools: list[MagicMock]) -> AsyncMock:
    fake_resp = MagicMock(tools=tools)
    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()
    fake_session.list_tools = AsyncMock(return_value=fake_resp)
    return fake_session


def _fake_tool(name: str, description: str, schema: dict) -> MagicMock:
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = schema
    return t


@pytest.mark.asyncio
async def test_returns_serialized_tools_on_success_sse(sse_server: MCPServer) -> None:
    tool = _fake_tool(
        "echo", "Echo input", {"type": "object", "properties": {"text": {"type": "string"}}}
    )
    fake_session = _fake_session([tool])

    with (
        patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse,
        patch("cubebox.mcp.cubepi_admin_discovery.ClientSession") as m_cs,
    ):
        m_sse.return_value.__aenter__.return_value = ("r", "w")
        m_cs.return_value.__aenter__.return_value = fake_session

        ok, tools, err = await discover_tools_metadata(sse_server, credential_or_token="tok-abc")

    assert ok is True
    assert err is None
    assert tools == [
        {
            "name": "echo",
            "description": "Echo input",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        }
    ]


@pytest.mark.asyncio
async def test_streamable_http_transport_uses_streamablehttp_client(
    streamable_http_server: MCPServer,
) -> None:
    tool = _fake_tool("echo", "Echo", {"type": "object"})
    fake_session = _fake_session([tool])

    with (
        patch("cubebox.mcp.cubepi_admin_discovery.streamablehttp_client") as m_sh,
        patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse,
        patch("cubebox.mcp.cubepi_admin_discovery.ClientSession") as m_cs,
    ):
        # streamablehttp_client yields 3-tuple (read, write, get_session_id)
        m_sh.return_value.__aenter__.return_value = ("r", "w", lambda: None)
        m_cs.return_value.__aenter__.return_value = fake_session

        ok, tools, err = await discover_tools_metadata(
            streamable_http_server, credential_or_token="tok-abc"
        )

    assert ok is True
    assert err is None
    assert tools is not None and len(tools) == 1
    # streamable_http path used, SSE path not touched
    m_sh.assert_called_once()
    m_sse.assert_not_called()


@pytest.mark.asyncio
async def test_returns_error_on_exception(sse_server: MCPServer) -> None:
    with patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse:
        m_sse.side_effect = RuntimeError("connection refused")
        ok, tools, err = await discover_tools_metadata(sse_server, credential_or_token=None)

    assert ok is False
    assert tools is None
    assert err is not None
    assert "connection refused" in err


@pytest.mark.asyncio
async def test_authorization_header_set_when_token_given(sse_server: MCPServer) -> None:
    fake_session = _fake_session([])

    with (
        patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse,
        patch("cubebox.mcp.cubepi_admin_discovery.ClientSession") as m_cs,
    ):
        m_sse.return_value.__aenter__.return_value = ("r", "w")
        m_cs.return_value.__aenter__.return_value = fake_session

        await discover_tools_metadata(sse_server, credential_or_token="tok-abc")

    # First positional arg = url; kwargs include headers.
    _, kwargs = m_sse.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok-abc"
