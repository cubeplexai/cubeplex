"""Unit tests for cubepi_admin_refresh — persist discovery result to the DB row."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from cubebox.mcp.cubepi_admin_refresh import refresh_tools_for_server_with_token
from cubebox.models import MCPServer


@pytest.fixture
def server() -> MCPServer:
    return MCPServer(
        id="mcp-1",
        org_id="org-1",
        name="srv",
        server_url="https://srv/sse",
        server_url_hash="hash",
        transport="sse",
        auth_method="bearer",
        credential_scope="org",
        owner_workspace_id=None,
        credential_id=None,
        authed=False,
        tools_cache=[],
        headers={},
        created_by_user_id="user-1",
    )


@pytest.mark.asyncio
async def test_success_updates_cache_and_authed(server: MCPServer) -> None:
    tools = [{"name": "t", "description": "", "input_schema": {}}]
    with patch(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        AsyncMock(return_value=(True, tools, None)),
    ):
        server_repo = AsyncMock()
        await refresh_tools_for_server_with_token(
            server, server_repo=server_repo, credential_or_token="x"
        )

    assert server.authed is True
    assert server.tools_cache == tools
    assert server.last_error is None
    assert isinstance(server.last_discovered_at, datetime)
    server_repo.update.assert_awaited_once_with(server)


@pytest.mark.asyncio
async def test_failure_persists_error(server: MCPServer) -> None:
    with patch(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        AsyncMock(return_value=(False, None, "boom")),
    ):
        server_repo = AsyncMock()
        await refresh_tools_for_server_with_token(
            server, server_repo=server_repo, credential_or_token=None
        )

    assert server.authed is False
    assert server.tools_cache == []
    assert server.last_error == "boom"
    server_repo.update.assert_awaited_once_with(server)


@pytest.mark.asyncio
async def test_orphan_citation_keys_removed_on_refresh(server: MCPServer) -> None:
    """After refresh, tool_citations keys whose tools vanished from tools_cache
    are stripped, and the notice is written to last_error."""
    # Pre-existing state: two citation mappings, one of which points at a tool
    # the upstream server no longer offers.
    server.tool_citations = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": None,
            "mapping": {"snippet": "s"},
        },
        "old_tool": {
            "content_type": "json",
            "source_type": "web",
            "content_field": None,
            "mapping": {"snippet": "s"},
        },
    }
    tools = [{"name": "web_search", "description": "", "input_schema": {}}]
    with patch(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        AsyncMock(return_value=(True, tools, None)),
    ):
        server_repo = AsyncMock()
        await refresh_tools_for_server_with_token(
            server, server_repo=server_repo, credential_or_token="x"
        )

    assert set(server.tool_citations.keys()) == {"web_search"}
    assert server.last_error is not None
    assert "old_tool" in server.last_error


@pytest.mark.asyncio
async def test_refresh_no_orphans_keeps_last_error_none(server: MCPServer) -> None:
    """When all existing citation keys still exist in the refreshed tools_cache,
    last_error stays None on success."""
    server.tool_citations = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": None,
            "mapping": {"snippet": "s"},
        },
    }
    tools = [
        {"name": "web_search", "description": "", "input_schema": {}},
        {"name": "newly_added_tool", "description": "", "input_schema": {}},
    ]
    with patch(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        AsyncMock(return_value=(True, tools, None)),
    ):
        server_repo = AsyncMock()
        await refresh_tools_for_server_with_token(
            server, server_repo=server_repo, credential_or_token="x"
        )

    assert set(server.tool_citations.keys()) == {"web_search"}
    assert server.last_error is None  # no orphans, no notice
