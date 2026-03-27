"""Tool system module"""

import asyncio

import nest_asyncio
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

    Uses nest_asyncio to allow running async code synchronously.
    Any failure is caught and logged as a warning — MCP errors never
    prevent the system from starting.
    """
    try:
        from cubebox.config import config

        if not config.get("mcp.enabled", False):
            logger.debug("MCP is disabled, skipping MCP tool loading")
            return

        from cubebox.mcp.client import MCPManager

        nest_asyncio.apply()
        manager = MCPManager()
        loop = asyncio.get_event_loop()
        tools: list[BaseTool] = loop.run_until_complete(manager.load_tools())

        for tool in tools:
            _registry.register_tool(tool)

        logger.info("Loaded {} MCP tool(s) into registry", len(tools))

    except Exception as e:
        logger.warning(
            "Failed to load MCP tools: {}. Continuing without MCP tools.", str(e)
        )


_load_mcp_tools()


def get_registry() -> ToolRegistry:
    """
    Get the global tool registry instance.

    Returns:
        ToolRegistry instance with all registered tools.
    """
    return _registry


__all__ = ["ToolRegistry", "get_registry"]
