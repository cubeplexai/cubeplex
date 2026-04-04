"""Convert LangGraph stream chunks to serializable event dicts.

Uses dual stream_mode=["messages", "updates"]:
- "messages" chunks: real-time text_delta and reasoning events
- "updates" chunks: complete tool_call and tool_result events (no accumulation needed)
"""

import json
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


def convert_messages_chunk(
    chunk: Any,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a ``stream_mode="messages"`` chunk to text/reasoning events.

    Only emits ``text_delta`` and ``reasoning`` events.  Tool calls and tool
    results are handled by :func:`convert_updates_chunk` instead, which
    receives complete messages after each graph node finishes.
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
        tool_name = msg.get("name")
    else:
        content = getattr(msg, "content", "") or ""
        additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
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

    # Text content (skip ToolMessages — they have a name attribute)
    if content and not tool_name:
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


def convert_updates_chunk(
    update: dict[str, Any],
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a ``stream_mode="updates"`` chunk to tool_call/tool_result events.

    ``updates`` mode yields ``{node_name: state_update}`` dicts after each graph
    node completes, containing full ``AIMessage`` / ``ToolMessage`` objects with
    complete ``tool_calls`` (args already parsed).
    """
    timestamp = datetime.now(UTC).isoformat()
    events: list[dict[str, Any]] = []

    if not isinstance(update, dict):
        return events

    for _node_name, state_update in update.items():
        messages = state_update.get("messages", []) if isinstance(state_update, dict) else []
        for msg in messages:
            _extract_tool_events(msg, timestamp, agent_id, events)

    return events


def _extract_tool_events(
    msg: Any,
    timestamp: str,
    agent_id: str | None,
    events: list[dict[str, Any]],
) -> None:
    """Extract tool_call and tool_result events from a complete message."""
    if isinstance(msg, dict):
        tool_calls = msg.get("tool_calls", [])
        content = msg.get("content", "")
        tool_name = msg.get("name")
        tool_call_id = msg.get("tool_call_id", "")
    else:
        tool_calls = getattr(msg, "tool_calls", []) or []
        content = getattr(msg, "content", "") or ""
        tool_name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", "")

    # Tool calls (from AIMessage)
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
        from cubebox.tools import get_registry

        registry = get_registry()
        content_type = registry.get_content_type(tool_name)

        # Unwrap MCP content blocks
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

        # Emit additional artifact event for save_artifact tool results
        if tool_name == "save_artifact":
            try:
                parsed = json.loads(result_str)
                if "artifact" in parsed:
                    events.append(
                        {
                            "type": "artifact",
                            "timestamp": timestamp,
                            "data": {
                                "action": parsed.get("action", "created"),
                                "artifact": parsed["artifact"],
                            },
                            "agent_id": agent_id,
                        }
                    )
            except (json.JSONDecodeError, KeyError):
                pass
