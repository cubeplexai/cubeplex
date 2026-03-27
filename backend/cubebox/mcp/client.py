"""MCP (Model Context Protocol) Client

Manages connections to MCP servers and tool integration.
Uses langchain-mcp-adapters MultiServerMCPClient to connect to servers
and expose their tools as LangChain BaseTool instances.
"""

from typing import Any, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from loguru import logger


def _build_connection_params(
    server_name: str, server_config: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Build MultiServerMCPClient connection params for one server.

    Returns None if the transport is unsupported.
    """
    transport = server_config.get("transport")

    if transport in ("streamable_http", "sse"):
        url = server_config.get("url")
        if not url:
            logger.warning("MCP server '{}': missing required 'url' field, skipping", server_name)
            return None
        params: dict[str, Any] = {
            "url": url,
            "transport": transport,
        }
        key = server_config.get("key")
        if key:
            params["headers"] = {"Authorization": f"Bearer {key}"}
        return params

    elif transport == "stdio":
        command = server_config.get("command")
        if not command:
            logger.warning(
                "MCP server '{}': missing required 'command' field, skipping", server_name
            )
            return None
        params = {
            "command": command,
            "args": server_config.get("args", []),
            "transport": "stdio",
        }
        env = server_config.get("env")
        if env:
            params["env"] = env
        return params

    else:
        logger.warning(
            "MCP server '{}': unsupported transport '{}', skipping", server_name, transport
        )
        return None


class MCPManager:
    """
    Manager for MCP server connections.

    Wraps MultiServerMCPClient to connect to configured MCP servers,
    fetch their tools, and apply optional per-server tool filtering.
    """

    def __init__(self, servers: dict[str, Any] | None = None) -> None:
        """
        Initialize MCPManager.

        Args:
            servers: Dict of server configs keyed by server name.
                     If None, loads from dynaconf config.
        """
        self._server_configs: dict[str, Any] = {}

        if servers is not None:
            self._load_from_dict(servers)
        else:
            self._load_from_config()

    def _load_from_dict(self, servers: dict[str, Any]) -> None:
        """Load server configs from a dict (used in tests)."""
        for server_name, server_config in servers.items():
            if not server_config.get("enabled", True):
                logger.debug("MCP server '{}' is disabled, skipping", server_name)
                continue
            self._server_configs[server_name] = server_config

    def _load_from_config(self) -> None:
        """Load server configs from dynaconf config."""
        from cubebox.config import config

        if not config.get("mcp.enabled", True):
            logger.debug("MCP is globally disabled, skipping all servers")
            return

        servers = config.get("mcp.servers", {})
        if not servers:
            return
        self._load_from_dict(servers)

    async def load_tools(self) -> list[BaseTool]:
        """
        Connect to all enabled MCP servers and return their tools.

        Per-server failures are caught and logged as warnings — the method
        always returns whatever tools were successfully loaded.

        Returns:
            List of BaseTool instances from all reachable servers.
        """
        if not self._server_configs:
            return []

        all_tools: list[BaseTool] = []

        for server_name, server_config in self._server_configs.items():
            try:
                params = _build_connection_params(server_name, server_config)
                if params is None:
                    continue

                client = MultiServerMCPClient({server_name: cast(Connection, params)})
                tools: list[BaseTool] = await client.get_tools()

                # Filter to requested tool names if specified
                allowed: list[str] | None = server_config.get("tools")
                if allowed:
                    allowed_set = set(allowed)
                    tools = [t for t in tools if t.name in allowed_set]

                logger.info(
                    "MCP server '{}': loaded {} tool(s): {}",
                    server_name,
                    len(tools),
                    [t.name for t in tools],
                )
                all_tools.extend(tools)

            except Exception as e:
                logger.warning(
                    "MCP server '{}' failed to load tools: {}. Skipping.",
                    server_name,
                    str(e),
                )

        return all_tools
