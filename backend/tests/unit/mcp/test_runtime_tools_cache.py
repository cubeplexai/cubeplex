"""Unit tests for the tools_cache fast path in cubepi_runtime.

The eager MCP loader now builds AgentTools from the install's persisted
``tools_cache`` instead of a live ``initialize`` + ``tools/list`` round
trip per send. These tests protect the two invariants that matter:

1. **Schema parity** — a cache-built tool must expose the same name,
   description, and parameter schema the live loader would produce from
   the same descriptor, or the serialized tool payload sent to the LLM
   (and therefore the prompt-cache prefix) would drift between a
   cache-hit send and a live-load send.
2. **Fallback** — an empty/unusable cache returns None so the caller
   falls back to live discovery instead of silently dropping a server.
"""

from __future__ import annotations

from typing import Any

from cubeplex.mcp.cubepi_runtime import _build_tools_from_cache
from cubeplex.mcp.effective import MCPRuntimeConnectorSpec

_WEATHER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "City name"},
        "days": {"type": "integer"},
    },
    "required": ["city"],
}


def _make_spec(tools_cache: list[dict[str, Any]]) -> MCPRuntimeConnectorSpec:
    return MCPRuntimeConnectorSpec(
        connector_id="mcins_cache",
        name="weather",
        server_url="https://mcp.example.com/mcp",
        transport="streamable_http",
        auth_method="static",
        grant_scope="org",
        credential_id="cred_test",
        refresh_credential_id=None,
        tool_citations={},
        tools_cache=tools_cache,
    )


def test_cache_built_tool_matches_live_loader_shape() -> None:
    """If the cache-built tool schema drifted from what the live loader
    produces for the same descriptor, the LLM tool payload (and the
    prompt-cache prefix) would differ between cache-hit and live sends."""
    from cubepi.mcp._adapter import make_mcp_agent_tool

    entry = {
        "name": "get_weather",
        "description": "Look up the weather",
        "input_schema": _WEATHER_SCHEMA,
        "output_schema": None,
    }
    spec = _make_spec([entry])

    cached_tools = _build_tools_from_cache(
        spec=spec, headers={"Authorization": "Bearer t"}, server_url=spec.server_url
    )
    assert cached_tools is not None and len(cached_tools) == 1
    cached = cached_tools[0]

    async def _noop_call_remote(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [], "isError": False}

    live = make_mcp_agent_tool(
        name="get_weather",
        description="Look up the weather",
        input_schema=_WEATHER_SCHEMA,
        call_remote=_noop_call_remote,
    )

    assert cached.name == live.name
    assert cached.description == live.description
    assert cached.parameters.model_json_schema() == live.parameters.model_json_schema()


def test_cache_entry_without_schema_gets_empty_object_schema() -> None:
    spec = _make_spec([{"name": "ping", "description": None, "input_schema": None}])
    tools = _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url)
    assert tools is not None and len(tools) == 1
    assert tools[0].name == "ping"
    assert tools[0].description == ""


def test_empty_cache_returns_none_for_live_fallback() -> None:
    spec = _make_spec([])
    assert _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url) is None


def test_nameless_entries_are_skipped_and_all_nameless_falls_back() -> None:
    spec = _make_spec([{"description": "no name"}, {"name": ""}])
    assert _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url) is None
