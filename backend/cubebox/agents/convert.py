"""Convert LangChain message types to the API wire format."""

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def _consolidate_subagent_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate raw per-token subagent events into a consolidated summary.

    Returns:
        {"text": str, "tool_calls": list[dict], "reasoning": str}
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for evt in events:
        evt_type = evt.get("type")
        data = evt.get("data", {})
        if evt_type == "text_delta":
            text_parts.append(data.get("content", ""))
        elif evt_type == "reasoning":
            reasoning_parts.append(data.get("content", ""))
        elif evt_type == "tool_call":
            tool_calls.append(
                {
                    "name": data.get("name", ""),
                    "arguments": data.get("arguments", {}),
                }
            )

    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "reasoning": "".join(reasoning_parts),
    }


def convert_to_api_messages(lc_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert a list of LangChain messages to the API response format.

    LangChain type -> API role mapping:
      HumanMessage  -> "user"
      AIMessage     -> "assistant"
      ToolMessage   -> "tool"
    """
    result: list[dict[str, Any]] = []
    prev_timestamp: str | None = None

    for msg in lc_messages:
        if isinstance(msg, HumanMessage):
            ts = _get_timestamp(msg)
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "user",
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    "tool_calls": None,
                    "reasoning": None,
                    "name": None,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

        elif isinstance(msg, AIMessage):
            raw_content = msg.content
            text_content: str | None
            if isinstance(raw_content, list):
                text_parts = [
                    block["text"]
                    for block in raw_content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text_content = "".join(text_parts) or None
            else:
                text_content = raw_content or None

            tool_calls = None
            if msg.tool_calls:
                tool_calls = [
                    {
                        "name": tc["name"],
                        "arguments": tc["args"],
                        "tool_call_id": tc.get("id", ""),
                    }
                    for tc in msg.tool_calls
                ] or None

            reasoning = (msg.additional_kwargs or {}).get("reasoning_content")

            ts = _get_timestamp(msg)

            # Estimate reasoning duration from gap between previous message and this one
            reasoning_duration_ms: int | None = None
            if reasoning and prev_timestamp:
                try:
                    prev_dt = datetime.fromisoformat(prev_timestamp)
                    curr_dt = datetime.fromisoformat(ts)
                    delta_ms = int((curr_dt - prev_dt).total_seconds() * 1000)
                    if delta_ms > 0:
                        reasoning_duration_ms = delta_ms
                except (ValueError, TypeError):
                    pass

            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "assistant",
                    "content": text_content,
                    "tool_calls": tool_calls,
                    "reasoning": reasoning or None,
                    "reasoning_duration_ms": reasoning_duration_ms,
                    "name": None,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

        elif isinstance(msg, ToolMessage):
            raw_events = (msg.additional_kwargs or {}).get("subagent_events")
            subagent_events = _consolidate_subagent_events(raw_events) if raw_events else None
            ts = _get_timestamp(msg)
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "tool",
                    "content": (msg.content if isinstance(msg.content, str) else str(msg.content)),
                    "tool_calls": None,
                    "reasoning": None,
                    "name": msg.name,
                    "tool_call_id": getattr(msg, "tool_call_id", None),
                    "subagent_events": subagent_events,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

    return result


def _get_timestamp(msg: BaseMessage) -> str:
    ts = (msg.response_metadata or {}).get("created_at")
    return ts or datetime.now(UTC).isoformat()
