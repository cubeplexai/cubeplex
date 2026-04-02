"""Convert LangGraph stream chunks to serializable event dicts."""

from datetime import UTC, datetime
from typing import Any


def _unwrap_mcp_content(content: Any) -> str:
    """Extract text from MCP content blocks format.

    MCP tools return content as list[{"type": "text", "text": "..."}].
    This extracts and concatenates the text values.
    """
    if not isinstance(content, list):
        return str(content)
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return "\n".join(texts) if texts else str(content)


def convert_chunk_to_events(
    chunk: Any,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a LangGraph stream chunk to a list of serialized event dicts.

    Args:
        chunk: Raw LangGraph chunk from astream()
        agent_id: Agent identifier (None for main, "subagent:<id>" for subagents)

    Returns:
        List of dicts, each containing:
        - type: "text_delta", "reasoning", "tool_call", "tool_result", etc.
        - timestamp: ISO 8601
        - data: Event-specific dict (content, name, arguments, etc.)
        - agent_id: Optional agent identifier
    """
    timestamp = datetime.now(UTC).isoformat()
    events: list[dict[str, Any]] = []

    if not isinstance(chunk, tuple) or len(chunk) < 2:
        return events

    msg, _metadata = chunk

    # Handle both dict and message object
    if isinstance(msg, dict):
        content = msg.get("content", "")
        additional_kwargs = msg.get("additional_kwargs", {})
        tool_calls = msg.get("tool_calls", [])
        tool_name = msg.get("name")
    else:
        content = getattr(msg, "content", "") or ""
        additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
        tool_calls = getattr(msg, "tool_calls", []) or []
        tool_name = getattr(msg, "name", None)

    # Reasoning content
    reasoning_content = (additional_kwargs or {}).get("reasoning_content", "")
    if reasoning_content:
        events.append(
            {
                "type": "reasoning",
                "timestamp": timestamp,
                "data": {"content": reasoning_content},
                "agent_id": agent_id,
            }
        )

    # Tool calls
    if tool_calls:
        for tc in tool_calls:
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            if not tc_name:
                continue
            events.append(
                {
                    "type": "tool_call",
                    "timestamp": timestamp,
                    "data": {"tool_call_id": tc_id, "name": tc_name, "arguments": tc_args},
                    "agent_id": agent_id,
                }
            )

    # Tool result (ToolMessage: has name and content)
    if tool_name and content:
        tool_call_id = (
            msg.get("tool_call_id", "")
            if isinstance(msg, dict)
            else getattr(msg, "tool_call_id", "")
        )

        # Look up declared content_type from registry
        from cubebox.tools import get_registry

        registry = get_registry()
        content_type = registry.get_content_type(tool_name)

        # Unwrap MCP content blocks (list of {"type": "text", "text": "..."})
        if content_type and isinstance(content, list):
            result_str = _unwrap_mcp_content(content)
        elif isinstance(content, str):
            result_str = content
        else:
            result_str = str(content)

        data: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": result_str,
        }
        if content_type:
            data["content_type"] = content_type

        events.append(
            {
                "type": "tool_result",
                "timestamp": timestamp,
                "data": data,
                "agent_id": agent_id,
            }
        )
        return events

    # Text content
    if content:
        usage_metadata = (
            getattr(msg, "usage_metadata", {})
            if not isinstance(msg, dict)
            else msg.get("usage_metadata", {})
        )
        events.append(
            {
                "type": "text_delta",
                "timestamp": timestamp,
                "data": {
                    "content": content,
                    "usage": {
                        "input_tokens": (usage_metadata or {}).get("input_tokens", 0),
                        "output_tokens": (usage_metadata or {}).get("output_tokens", 0),
                    },
                },
                "agent_id": agent_id,
            }
        )

    return events
