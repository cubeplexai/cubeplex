"""Normalize MCP tool descriptors into the persisted tools_cache shape."""

from __future__ import annotations

from types import SimpleNamespace

from cubeplex.services.mcp_discovery import _tool_to_dict


def test_empty_object_schema_is_kept() -> None:
    """An empty ``{}`` schema is real payload, not a missing field.

    SDK 2 tools use snake_case ``input_schema``. Using ``a or b`` would
    drop ``{}`` and fall through to the camelCase alias (also missing),
    persisting ``input_schema: null`` and changing the LLM tool payload.
    """
    tool = SimpleNamespace(
        name="ping",
        description="",
        input_schema={},
        output_schema={},
    )
    assert _tool_to_dict(tool)["input_schema"] == {}
    assert _tool_to_dict(tool)["output_schema"] == {}


def test_camel_case_schema_alias() -> None:
    tool = SimpleNamespace(
        name="echo",
        description="echoes",
        inputSchema={"type": "object", "properties": {"text": {"type": "string"}}},
        outputSchema={"type": "object"},
    )
    dumped = _tool_to_dict(tool)
    assert dumped["input_schema"] == {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    assert dumped["output_schema"] == {"type": "object"}
