"""MCP manager for legacy config-backed servers.

DB-backed MCP servers are assembled per run and must not be registered in the
global tool registry. This module only handles the legacy ``mcp.servers`` config
path that is shared across all workspaces.
"""

from typing import Any, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from loguru import logger


def _build_legacy_connection_params(
    server_name: str,
    server_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Build MultiServerMCPClient connection params for a legacy config server."""
    transport = server_config.get("transport")

    if transport in ("streamable_http", "sse"):
        url = server_config.get("url")
        if not url:
            logger.warning("Legacy MCP '{}': missing required 'url'; skipping", server_name)
            return None
        params: dict[str, Any] = {"url": url, "transport": transport}
        key = server_config.get("key")
        if key:
            params["headers"] = {"Authorization": f"Bearer {key}"}
        return params

    if transport == "stdio":
        command = server_config.get("command")
        if not command:
            logger.warning(
                "Legacy MCP '{}': missing required 'command'; skipping",
                server_name,
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

    logger.warning("Legacy MCP '{}': unsupported transport '{}'; skipping", server_name, transport)
    return None


async def _load_tools_from_server_configs(
    server_configs: dict[str, Any],
) -> list[BaseTool]:
    """Connect to configured servers and return successfully loaded tools."""
    if not server_configs:
        return []

    all_tools: list[BaseTool] = []
    for server_name, server_config in server_configs.items():
        try:
            params = _build_legacy_connection_params(server_name, server_config)
            if params is None:
                continue

            client = MultiServerMCPClient({server_name: cast(Connection, params)})
            tools: list[BaseTool] = await client.get_tools()
            tools = _filter_and_annotate_tools(tools, server_config)

            logger.info(
                "Legacy MCP '{}': loaded {} tool(s): {}",
                server_name,
                len(tools),
                [tool.name for tool in tools],
            )
            all_tools.extend(tools)
        except Exception as exc:
            if isinstance(exc, BaseExceptionGroup):
                causes = "; ".join(str(sub) for sub in exc.exceptions)
                logger.warning(
                    "Legacy MCP '{}' failed to load tools: {} (causes: {}). Skipping.",
                    server_name,
                    exc,
                    causes,
                )
            else:
                logger.warning(
                    "Legacy MCP '{}' failed to load tools: {}. Skipping.",
                    server_name,
                    exc,
                )

    return all_tools


def _filter_and_annotate_tools(
    tools: list[BaseTool],
    server_config: dict[str, Any],
) -> list[BaseTool]:
    tool_defs: list[str | dict[str, Any]] | None = server_config.get("tools")
    if not tool_defs:
        return tools

    tool_config_map: dict[str, dict[str, Any]] = {}
    for tool_def in tool_defs:
        if isinstance(tool_def, str):
            tool_config_map[tool_def] = {}
        elif isinstance(tool_def, dict) and "name" in tool_def:
            tool_config_map[str(tool_def["name"])] = tool_def

    filtered = [tool for tool in tools if tool.name in tool_config_map]
    for tool in filtered:
        tool_config = tool_config_map.get(tool.name, {})
        content_type = tool_config.get("content_type")
        if content_type:
            if tool.metadata is None:
                tool.metadata = {}
            tool.metadata["content_type"] = str(content_type)
    return filtered


class MCPManager:
    """Legacy config-backed MCP manager.

    The class-level methods are used by app startup. The constructor and
    ``load_tools`` remain for existing tests and explicit legacy loaders.
    """

    _legacy_cache: list[BaseTool] | None = None

    def __init__(self, servers: dict[str, Any] | None = None) -> None:
        self._server_configs: dict[str, Any] = {}
        if servers is not None:
            self._load_from_dict(servers)
        else:
            self._load_from_config()

    def _load_from_dict(self, servers: dict[str, Any]) -> None:
        """Load enabled legacy server configs from a dict."""
        for server_name, server_config in servers.items():
            if not server_config.get("enabled", True):
                logger.debug("Legacy MCP '{}' is disabled; skipping", server_name)
                continue
            self._server_configs[server_name] = server_config

    def _load_from_config(self) -> None:
        """Load enabled legacy server configs from dynaconf."""
        from cubebox.config import config

        if not config.get("mcp.enabled", False):
            logger.debug("Legacy MCP config is disabled; skipping all servers")
            return
        servers = config.get("mcp.servers", {}) or {}
        self._load_from_dict(servers)

    async def load_tools(self) -> list[BaseTool]:
        """Load tools from this manager's server configs."""
        return await _load_tools_from_server_configs(self._server_configs)

    @classmethod
    async def load_legacy_config_servers(cls) -> list[BaseTool]:
        """Load legacy config servers once and return the process-wide cache."""
        if cls._legacy_cache is not None:
            return cls._legacy_cache

        manager = cls()
        cls._legacy_cache = await manager.load_tools()
        if cls._legacy_cache:
            logger.info(
                "Loaded {} legacy MCP tool(s) from config; DB MCP tools are run-scoped",
                len(cls._legacy_cache),
            )
        return cls._legacy_cache

    @classmethod
    def legacy_tools_cache(cls) -> list[BaseTool]:
        """Return cached legacy tools; assumes startup already loaded them."""
        return cls._legacy_cache or []
