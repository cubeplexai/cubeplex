"""cubepi event → cubebox SSE event dict translation (M1.3+).

Two translation layers:

1. ``convert_event_to_sse`` — low-level; translates a single
   cubepi ``StreamEvent`` (provider-level) into 0..1 cubebox SSE dicts.
   Used when the caller has direct access to the raw provider stream.

2. ``convert_agent_event_to_sse`` — high-level; translates a
   cubepi ``AgentEvent`` (agent-loop-level) into 0..N cubebox SSE dicts.
   Used by the run_manager cubepi dispatch path (M1.5+), which subscribes
   to the Agent's listener channel and receives AgentEvents.

cubebox SSE event types (consumed by frontend):
    text_delta, reasoning, tool_call, tool_call_delta, tool_result,
    usage, error, done.
"""

from __future__ import annotations

from typing import Any

from cubepi import AgentToolResult
from cubepi.agent.types import (
    AgentEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
)
from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    TextContent,
    ToolCall,
    UserMessage,
)


def _stringify_tool_result(result: Any) -> tuple[str, Any]:
    """Extract a string and details payload from a cubepi tool result.

    ``ToolExecutionEndEvent.result`` is typed ``Any`` but is in practice an
    ``AgentToolResult`` whose ``content`` is a list of cubepi content blocks
    (text/image/etc.). The previous implementation forwarded the model
    object as-is and let downstream ``str()`` produce a Pydantic repr —
    which broke frontend JSON parsers (e.g. ``save_artifact`` rendering
    fell through to a regular tool-call card instead of the artifact card).

    We concatenate ``TextContent.text`` blocks and surface
    ``AgentToolResult.details`` separately so the live SSE shape matches
    the post-reload one (``ToolResultMessage.details``).
    """
    if isinstance(result, AgentToolResult):
        # CitationMiddleware rewrites .content to 【N-M】-marked chunk text for
        # the LLM and stashes the pre-rewrite raw output in
        # details["original_content"] so the frontend preview can still parse
        # the original (e.g. JSON for web_search). Prefer it when present.
        details = result.details
        if isinstance(details, dict) and isinstance(details.get("original_content"), str):
            return details["original_content"], details
        text = "".join(b.text for b in result.content if isinstance(b, TextContent))
        return text, details
    if isinstance(result, str):
        return result, None
    if result is None:
        return "", None
    return str(result), None


def convert_event_to_sse(evt: StreamEvent) -> list[dict[str, Any]]:
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


def convert_agent_event_to_sse(evt: AgentEvent) -> list[dict[str, Any]]:
    """Translate a single cubepi AgentEvent into 0..N cubebox SSE event dicts.

    The cubepi Agent exposes AgentEvents via its subscribe() listener channel.
    AgentEvents are higher-level than StreamEvents:

    - ``MessageUpdateEvent`` wraps a ``stream_event: StreamEvent`` — we unwrap
      and delegate to ``convert_event_to_sse`` for text/thinking/tool deltas.
    - ``ToolExecutionEndEvent`` carries the completed tool result — translated to
      a ``tool_result`` SSE dict.
    - ``AgentEndEvent`` emits ``done``.
    - All other AgentEvents (agent_start, turn_start/end, message_start/end,
      tool_execution_start/update) are silently dropped; they carry no content
      that cubebox's frontend currently needs.
    """
    if isinstance(evt, MessageUpdateEvent):
        return convert_event_to_sse(evt.stream_event)

    if isinstance(evt, ToolExecutionEndEvent):
        text, details = _stringify_tool_result(evt.result)
        return [
            {
                "type": "tool_result",
                "tool_call_id": evt.tool_call_id,
                "name": evt.tool_name,
                "result": text,
                "details": details,
                "is_error": evt.is_error,
            }
        ]

    if isinstance(evt, MessageEndEvent) and isinstance(evt.message, AssistantMessage):
        msg = evt.message
        if msg.usage is not None and msg.usage.input_tokens > 0:
            return [
                {
                    "type": "usage",
                    "input_tokens": msg.usage.input_tokens,
                    "output_tokens": msg.usage.output_tokens or 0,
                    "cache_read_tokens": msg.usage.cache_read_tokens or 0,
                    "cache_write_tokens": msg.usage.cache_write_tokens or 0,
                }
            ]

    if isinstance(evt, MessageEndEvent) and isinstance(evt.message, UserMessage):
        steer_id = evt.message.metadata.get("steer_id")
        if steer_id:
            text = "".join(c.text for c in evt.message.content if isinstance(c, TextContent))
            return [{"type": "injected_message", "content": text, "steer_id": steer_id}]

    # Silently drop all other AgentEvent types:
    # AgentStartEvent, AgentEndEvent (done is emitted by run_manager with usage),
    # TurnStartEvent, TurnEndEvent, MessageStartEvent,
    # ToolExecutionStartEvent, ToolExecutionUpdateEvent
    return []
