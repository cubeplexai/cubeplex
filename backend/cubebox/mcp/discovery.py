"""MCP discovery and cached tool reconstruction."""

from typing import Any, NoReturn, cast

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from loguru import logger
from pydantic import BaseModel, ConfigDict

from cubebox.mcp.connection_params import build_connection_params
from cubebox.models import MCPServer

_CACHE_SERVER_NAME = "server"


async def discover_tools(
    server: MCPServer,
    *,
    credential_or_token: str | None,
) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    """Connect, list tools, and return success plus serialized tools or error."""
    try:
        params = build_connection_params(server, credential_or_token=credential_or_token)
    except ValueError as exc:
        return False, None, f"params build failed: {exc}"

    try:
        client = MultiServerMCPClient({server.name: cast_to_connection(params)})
        raw_tools: list[BaseTool] = await client.get_tools(server_name=server.name)
        return True, [serialize_tool(tool) for tool in raw_tools], None
    except Exception as exc:
        if isinstance(exc, BaseExceptionGroup):
            causes = "; ".join(str(sub) for sub in exc.exceptions)
            return False, None, f"{exc}; causes: {causes}"
        return False, None, str(exc)


def cast_to_connection(params: dict[str, Any]) -> Connection:
    """Centralize the TypedDict cast required by langchain-mcp-adapters."""
    return cast(Connection, params)


def serialize_tool(tool: BaseTool) -> dict[str, Any]:
    """Extract name, description, and input schema as a JSON-safe dict."""
    schema: dict[str, Any] = {}
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        if hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        elif hasattr(args_schema, "schema"):
            schema = args_schema.schema()

    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema,
    }


def construct_basetools_from_cache(
    cache: list[dict[str, Any]],
    connection_params: dict[str, Any],
) -> list[BaseTool]:
    """Build LangChain tools from serialized cache entries."""
    tools: list[BaseTool] = []
    for entry in cache:
        try:
            tools.append(_build_basetool_for_entry(entry, connection_params))
        except Exception as exc:
            logger.warning(
                "MCP cache entry '{}' deserialization failed: {}",
                entry.get("name"),
                exc,
            )
    return tools


def _build_basetool_for_entry(
    entry: dict[str, Any],
    connection_params: dict[str, Any],
) -> BaseTool:
    name = str(entry["name"])
    description = str(entry.get("description", ""))
    input_schema = entry.get("input_schema", {"type": "object", "properties": {}})

    async def _ainvoke(**kwargs: Any) -> Any:
        client = MultiServerMCPClient({_CACHE_SERVER_NAME: cast_to_connection(connection_params)})
        tools = await client.get_tools(server_name=_CACHE_SERVER_NAME)
        for tool in tools:
            if tool.name == name:
                return await tool.ainvoke(kwargs)
        raise ValueError(f"MCP tool '{name}' not found")

    return StructuredTool.from_function(
        func=_sync_wrapper,
        coroutine=_ainvoke,
        name=name,
        description=description,
        args_schema=_dict_to_pydantic(name, input_schema),
    )


def _sync_wrapper(**_kwargs: Any) -> NoReturn:
    """Sync path is unsupported; MCP tools are invoked asynchronously."""
    raise RuntimeError("MCP tools must be invoked asynchronously")


def _dict_to_pydantic(name: str, _schema: Any) -> type[BaseModel]:
    """Return a permissive args model for cached JSON schema entries."""

    class _Permissive(BaseModel):
        model_config = ConfigDict(extra="allow")

    _Permissive.__name__ = f"{name}Args"
    return _Permissive
