"""E2E tests for MCP tool loading."""

import pytest

from cubebox.mcp.client import MCPManager


class TestMCPManager:
    """Tests for MCPManager tool loading behavior."""

    def test_load_tools_disabled_server_skipped(self) -> None:
        """A server with enabled=false is not connected."""
        manager = MCPManager(
            servers={
                "disabled_server": {
                    "url": "http://localhost:9999/unreachable",
                    "transport": "streamable_http",
                    "enabled": False,
                }
            }
        )
        # Should have no server configs loaded
        assert manager._server_configs == {}

    @pytest.mark.asyncio
    async def test_load_tools_unreachable_server_fails_gracefully(self) -> None:
        """An unreachable server logs a warning and returns empty list."""
        manager = MCPManager(
            servers={
                "bad_server": {
                    "url": "http://localhost:19999/nonexistent",
                    "transport": "streamable_http",
                    "enabled": True,
                }
            }
        )
        # Should not raise — graceful failure
        tools = await manager.load_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_load_tools_filters_by_tool_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When tools list is configured, only listed tools are returned."""
        from unittest.mock import AsyncMock, MagicMock

        # Create two fake tools
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_b = MagicMock()
        tool_b.name = "tool_b"

        # Patch MultiServerMCPClient.get_tools to return both tools
        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[tool_a, tool_b])

        import cubebox.mcp.client as mcp_module
        monkeypatch.setattr(mcp_module, "MultiServerMCPClient", lambda params: mock_client)

        manager = MCPManager(
            servers={
                "test_server": {
                    "url": "http://localhost:8020/api",
                    "transport": "streamable_http",
                    "enabled": True,
                    "tools": ["tool_a"],  # only tool_a requested
                }
            }
        )
        tools = await manager.load_tools()
        assert len(tools) == 1
        assert tools[0].name == "tool_a"

    @pytest.mark.asyncio
    async def test_load_tools_no_filter_returns_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no tools list configured, all tools from the server are returned."""
        from unittest.mock import AsyncMock, MagicMock

        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_b = MagicMock()
        tool_b.name = "tool_b"

        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[tool_a, tool_b])

        import cubebox.mcp.client as mcp_module
        monkeypatch.setattr(mcp_module, "MultiServerMCPClient", lambda params: mock_client)

        manager = MCPManager(
            servers={
                "test_server": {
                    "url": "http://localhost:8020/api",
                    "transport": "streamable_http",
                    "enabled": True,
                    # no 'tools' key → load all
                }
            }
        )
        tools = await manager.load_tools()
        assert len(tools) == 2
