"""Unit tests for cubepi_admin_discovery — list tools via raw mcp SDK."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.mcp.cubepi_admin_discovery import discover_tools_metadata
from cubebox.models import MCPServer


@pytest.fixture
def http_server() -> MCPServer:
    return MCPServer(
        id="mcp-test1",
        org_id="org-test",
        name="test-server",
        server_url="https://mcp.example.com/sse",
        server_url_hash="hash",
        transport="sse",
        auth_method="bearer",
        credential_scope="org",
        owner_workspace_id=None,
        credential_id=None,
        authed=False,
        tools_cache=[],
        headers={},
        created_by_user_id="user-test",
    )


@pytest.mark.asyncio
async def test_returns_serialized_tools_on_success(http_server: MCPServer) -> None:
    fake_tool = MagicMock()
    fake_tool.name = "echo"
    fake_tool.description = "Echo input"
    fake_tool.inputSchema = {"type": "object", "properties": {"text": {"type": "string"}}}

    fake_resp = MagicMock(tools=[fake_tool])
    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()
    fake_session.list_tools = AsyncMock(return_value=fake_resp)

    with (
        patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse,
        patch("cubebox.mcp.cubepi_admin_discovery.ClientSession") as m_cs,
    ):
        m_sse.return_value.__aenter__.return_value = ("r", "w")
        m_cs.return_value.__aenter__.return_value = fake_session

        ok, tools, err = await discover_tools_metadata(http_server, credential_or_token="tok-abc")

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
async def test_returns_error_on_exception(http_server: MCPServer) -> None:
    with patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse:
        m_sse.side_effect = RuntimeError("connection refused")
        ok, tools, err = await discover_tools_metadata(http_server, credential_or_token=None)

    assert ok is False
    assert tools is None
    assert err is not None
    assert "connection refused" in err


@pytest.mark.asyncio
async def test_authorization_header_set_when_token_given(http_server: MCPServer) -> None:
    fake_resp = MagicMock(tools=[])
    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()
    fake_session.list_tools = AsyncMock(return_value=fake_resp)

    with (
        patch("cubebox.mcp.cubepi_admin_discovery.sse_client") as m_sse,
        patch("cubebox.mcp.cubepi_admin_discovery.ClientSession") as m_cs,
    ):
        m_sse.return_value.__aenter__.return_value = ("r", "w")
        m_cs.return_value.__aenter__.return_value = fake_session

        await discover_tools_metadata(http_server, credential_or_token="tok-abc")

    # First positional arg = url; kwargs include headers.
    _, kwargs = m_sse.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok-abc"
