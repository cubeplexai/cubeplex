"""Tool system module"""

import asyncio

from langchain_core.tools import BaseTool
from loguru import logger

from cubebox.tools.builtin.calculator import create_calculator_tool
from cubebox.tools.registry import ToolRegistry

# Create global tool registry instance
_registry = ToolRegistry()

# Register built-in tools
_registry.register_tool(create_calculator_tool())


def _load_mcp_tools() -> None:
    """
    Load MCP tools into the registry at module init.

    Runs the async MCP manager in a new event loop.
    Any failure is caught and logged as a warning — MCP errors never
    prevent the system from starting.
    """
    try:
        from cubebox.config import config

        if not config.get("mcp.enabled", False):
            logger.debug("MCP is disabled, skipping MCP tool loading")
            return

        from cubebox.mcp.client import MCPManager

        async def _load() -> list[BaseTool]:
            manager = MCPManager()
            return await manager.load_tools()

        tools: list[BaseTool] = asyncio.run(_load())

        for tool in tools:
            _registry.register_tool(tool)

        logger.info("Loaded {} MCP tool(s) into registry", len(tools))

    except Exception as e:
        logger.warning("Failed to load MCP tools: {}. Continuing without MCP tools.", str(e))


_load_mcp_tools()


def get_registry() -> ToolRegistry:
    """
    Get the global tool registry instance.

    Returns:
        ToolRegistry instance with all registered tools.
    """
    return _registry


__all__ = ["ToolRegistry", "get_registry"]
