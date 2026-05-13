"""stream_pi tests — cubepi StreamEvent → cubebox SSE (M1.3)."""

from cubepi.agent.types import MessageEndEvent
from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    TextContent,
    ToolCall,
    Usage,
)

from cubebox.agents.stream_pi import convert_cubepi_agent_event_to_sse, convert_cubepi_event_to_sse


def _mk_assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = []
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, usage=Usage())


def test_text_delta_translates_to_text_delta() -> None:
    evt = StreamEvent(type="text_delta", delta="hello", partial=_mk_assistant("hello"))
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "text_delta", "delta": "hello"}]


def test_thinking_delta_translates_to_reasoning() -> None:
    evt = StreamEvent(type="thinking_delta", delta="thinking...", partial=_mk_assistant())
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "reasoning", "delta": "thinking..."}]


def test_toolcall_end_emits_tool_call() -> None:
    """toolcall_end → fully-formed tool_call (id+name+arguments)."""
    tc = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    partial = _mk_assistant(tool_calls=[tc])
    evt = StreamEvent(type="toolcall_end", content_index=0, partial=partial)
    out = convert_cubepi_event_to_sse(evt)
    assert len(out) == 1
    assert out[0]["type"] == "tool_call"
    assert out[0]["id"] == "tc1"
    assert out[0]["name"] == "search"
    assert out[0]["arguments"] == {"q": "x"}


def test_toolcall_end_missing_partial_drops() -> None:
    """Defensive: toolcall_end without partial → empty list."""
    evt = StreamEvent(type="toolcall_end", content_index=0)
    out = convert_cubepi_event_to_sse(evt)
    assert out == []


def test_toolcall_delta_emits_tool_call_delta() -> None:
    evt = StreamEvent(
        type="toolcall_delta",
        delta='{"q": "x"',
        partial=_mk_assistant(tool_calls=[ToolCall(id="tc1", name="search", arguments={})]),
        content_index=0,
    )
    out = convert_cubepi_event_to_sse(evt)
    assert out[0]["type"] == "tool_call_delta"
    assert out[0]["delta"] == '{"q": "x"'


def test_done_translates_to_done() -> None:
    evt = StreamEvent(type="done")
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "done"}]


def test_error_translates_to_error() -> None:
    evt = StreamEvent(type="error", error_message="boom")
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "error", "error": "boom"}]


def test_error_with_missing_message_has_fallback() -> None:
    evt = StreamEvent(type="error")
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "error", "error": "unknown error"}]


def test_silent_events_are_dropped() -> None:
    """Events with no cubebox SSE equivalent return empty list."""
    for t in [
        "text_start",
        "text_end",
        "thinking_start",
        "thinking_end",
        "toolcall_start",
        "start",
    ]:
        evt = StreamEvent(type=t)
        out = convert_cubepi_event_to_sse(evt)
        assert out == [], f"event type {t!r} should be silently dropped, got {out!r}"


# ---------------------------------------------------------------------------
# convert_cubepi_agent_event_to_sse — MessageEndEvent → usage


def test_message_end_with_usage_emits_usage_event() -> None:
    """MessageEndEvent carrying AssistantMessage with usage → usage SSE dict."""
    msg = AssistantMessage(
        content=[],
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=3,
            cache_write_tokens=2,
        ),
    )
    evt = MessageEndEvent(message=msg)
    out = convert_cubepi_agent_event_to_sse(evt)
    assert len(out) == 1
    assert out[0] == {
        "type": "usage",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
    }


def test_message_end_with_zero_input_tokens_is_dropped() -> None:
    """MessageEndEvent with input_tokens=0 produces no usage event (intermediate chunk)."""
    msg = AssistantMessage(
        content=[],
        usage=Usage(input_tokens=0, output_tokens=0),
    )
    evt = MessageEndEvent(message=msg)
    out = convert_cubepi_agent_event_to_sse(evt)
    assert out == []


def test_message_end_with_none_usage_is_dropped() -> None:
    """MessageEndEvent with usage=None produces no usage event."""
    msg = AssistantMessage(content=[], usage=None)
    evt = MessageEndEvent(message=msg)
    out = convert_cubepi_agent_event_to_sse(evt)
    assert out == []
