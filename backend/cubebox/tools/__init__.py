"""Tool system module"""

from cubebox.tools.builtin.calculator import create_calculator_tool
from cubebox.tools.builtin.datetime_tool import create_datetime_tool
from cubebox.tools.registry import ToolRegistry

# Create global tool registry instance
_registry = ToolRegistry()

# Register built-in tools (load_skill is request-scoped — wired in agents/graph.py).
# DB-backed MCP tools are assembled per (workspace, user, run) in
# cubebox.mcp.runtime; they are intentionally NOT registered here.
_registry.register_tool(create_calculator_tool())
_registry.register_tool(create_datetime_tool())


def get_registry() -> ToolRegistry:
    """
    Get the global tool registry instance.

    Returns:
        ToolRegistry instance with all registered tools.
    """
    return _registry


__all__ = ["ToolRegistry", "get_registry"]
