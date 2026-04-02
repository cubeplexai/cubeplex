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

        if not config.get("mcp.enabled", False):
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

                # Filter to requested tool names and apply per-tool config
                tool_defs: list[str | dict[str, Any]] | None = server_config.get("tools")
                if tool_defs:
                    # Build lookup: tool_name -> {content_type, ...}
                    tool_config_map: dict[str, dict[str, Any]] = {}
                    for td in tool_defs:
                        if isinstance(td, str):
                            tool_config_map[td] = {}
                        elif isinstance(td, dict) and "name" in td:
                            tool_config_map[str(td["name"])] = td
                    tools = [t for t in tools if t.name in tool_config_map]

                    # Merge per-tool config into tool metadata
                    for tool in tools:
                        tc = tool_config_map.get(tool.name, {})
                        content_type = tc.get("content_type")
                        if content_type:
                            if tool.metadata is None:
                                tool.metadata = {}
                            tool.metadata["content_type"] = str(content_type)

                logger.info(
                    "MCP server '{}': loaded {} tool(s): {}",
                    server_name,
                    len(tools),
                    [t.name for t in tools],
                )
                all_tools.extend(tools)

            except Exception as e:
                cause = e
                if isinstance(e, BaseExceptionGroup):
                    causes = "; ".join(str(sub) for sub in e.exceptions)
                    logger.warning(
                        "MCP server '{}' failed to load tools: {} (causes: {}). Skipping.",
                        server_name,
                        str(e),
                        causes,
                    )
                else:
                    logger.warning(
                        "MCP server '{}' failed to load tools: {}. Skipping.",
                        server_name,
                        str(cause),
                    )

        return all_tools
