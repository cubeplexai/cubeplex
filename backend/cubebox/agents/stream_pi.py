"""cubepi StreamEvent → cubebox SSE event dict translation (M1.3).

cubebox's SSE event types (consumed by frontend): text_delta, reasoning,
tool_call, tool_call_delta, tool_result, usage, error, done.

cubepi's provider events are richer: text_start/delta/end, thinking_*,
toolcall_*, start, done, error. This module maps the subset cubebox cares
about and silently drops the rest.

`tool_result` events come from the agent loop's after_tool_call path,
not the provider stream — emitted separately when the loop captures a
tool execution result. M3 will wire that path; M1 only handles provider
stream events.
"""

from __future__ import annotations

from typing import Any

from cubepi.providers.base import StreamEvent, ToolCall


def convert_cubepi_event_to_sse(evt: StreamEvent) -> list[dict[str, Any]]:
    """Translate a single cubepi StreamEvent into 0..1 cubebox SSE event dicts."""
    t = evt.type

    if t == "text_delta":
        return [{"type": "text_delta", "delta": evt.delta or ""}]

    if t == "thinking_delta":
        return [{"type": "reasoning", "delta": evt.delta or ""}]

    if t == "toolcall_delta":
        return [{"type": "tool_call_delta", "delta": evt.delta or ""}]

    if t == "toolcall_end":
        if evt.partial is None or evt.content_index is None:
            return []
        try:
            block = evt.partial.content[evt.content_index]
        except (IndexError, TypeError):
            return []
        if not isinstance(block, ToolCall):
            return []
        return [
            {
                "type": "tool_call",
                "id": block.id,
                "name": block.name,
                "arguments": block.arguments,
            }
        ]

    if t == "done":
        return [{"type": "done"}]

    if t == "error":
        return [{"type": "error", "error": evt.error_message or "unknown error"}]

    # Silent: start, text_start/end, thinking_start/end, toolcall_start
    return []
