"""Tool Registry

Manages registration and retrieval of tools for agents.
Supports both built-in tools and MCP-provided tools.
"""

from langchain_core.tools import BaseTool


class ToolRegistry:
    """Registry for managing agent tools"""

    def __init__(self) -> None:
        """Initialize the tool registry"""
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """
        Register a tool.

        Args:
            tool: BaseTool instance to register
        """
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool | None:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            BaseTool instance or None if not found
        """
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """
        List all registered tools.

        Returns:
            List of BaseTool instances
        """
        return list(self._tools.values())

    def list_tool_names(self) -> list[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())
