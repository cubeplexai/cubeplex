"""Unit tests for MCP tool serialize round-trip."""

from typing import Any

from langchain_core.tools import StructuredTool

from cubebox.mcp.discovery import construct_basetools_from_cache, serialize_tool


def _dummy_tool(name: str, description: str) -> StructuredTool:
    def _fn(query: str) -> str:
        return f"echo: {query}"

    return StructuredTool.from_function(
        func=_fn,
        name=name,
        description=description,
    )


def test_serialize_returns_dict_with_required_fields() -> None:
    tool = _dummy_tool("echo", "Echoes input")

    blob = serialize_tool(tool)

    assert blob["name"] == "echo"
    assert blob["description"] == "Echoes input"
    assert "input_schema" in blob


def test_construct_returns_basetools_with_correct_metadata() -> None:
    cache: list[dict[str, Any]] = [
        {
            "name": "a",
            "description": "tool a",
            "input_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
        {
            "name": "b",
            "description": "tool b",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    ]
    params = {"url": "https://srv", "transport": "streamable_http"}

    tools = construct_basetools_from_cache(cache, params)

    assert len(tools) == 2
    assert {tool.name for tool in tools} == {"a", "b"}
