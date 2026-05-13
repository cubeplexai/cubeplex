"""cubepi event → cubebox SSE event dict translation (M1.3+).

Two translation layers:

1. ``convert_cubepi_event_to_sse`` — low-level; translates a single
   cubepi ``StreamEvent`` (provider-level) into 0..1 cubebox SSE dicts.
   Used when the caller has direct access to the raw provider stream.

2. ``convert_cubepi_agent_event_to_sse`` — high-level; translates a
   cubepi ``AgentEvent`` (agent-loop-level) into 0..N cubebox SSE dicts.
   Used by the run_manager cubepi dispatch path (M1.5+), which subscribes
   to the Agent's listener channel and receives AgentEvents.

cubebox SSE event types (consumed by frontend):
    text_delta, reasoning, tool_call, tool_call_delta, tool_result,
    usage, error, done.
"""

from __future__ import annotations

from typing import Any

from cubepi.agent.types import (
    AgentEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
)
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


def convert_cubepi_agent_event_to_sse(evt: AgentEvent) -> list[dict[str, Any]]:
    """Translate a single cubepi AgentEvent into 0..N cubebox SSE event dicts.

    The cubepi Agent exposes AgentEvents via its subscribe() listener channel.
    AgentEvents are higher-level than StreamEvents:

    - ``MessageUpdateEvent`` wraps a ``stream_event: StreamEvent`` — we unwrap
      and delegate to ``convert_cubepi_event_to_sse`` for text/thinking/tool deltas.
    - ``ToolExecutionEndEvent`` carries the completed tool result — translated to
      a ``tool_result`` SSE dict.
    - ``AgentEndEvent`` emits ``done``.
    - All other AgentEvents (agent_start, turn_start/end, message_start/end,
      tool_execution_start/update) are silently dropped; they carry no content
      that cubebox's frontend currently needs.
    """
    if isinstance(evt, MessageUpdateEvent):
        return convert_cubepi_event_to_sse(evt.stream_event)

    if isinstance(evt, ToolExecutionEndEvent):
        return [
            {
                "type": "tool_result",
                "tool_call_id": evt.tool_call_id,
                "name": evt.tool_name,
                "result": evt.result,
                "is_error": evt.is_error,
            }
        ]

    # Silently drop all other AgentEvent types:
    # AgentStartEvent, AgentEndEvent (done is emitted by run_manager with usage),
    # TurnStartEvent, TurnEndEvent, MessageStartEvent, MessageEndEvent,
    # ToolExecutionStartEvent, ToolExecutionUpdateEvent
    return []
