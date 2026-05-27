"""Unit tests for ``cubepi_dict_to_agent_event`` in run_manager.

Regression gate for the bug discovered during M5.3 diagnosis: error
dicts emitted by ``convert_agent_event_to_sse`` were silently
dropped by the cubepi dispatch loop, masking real failures (e.g. an
auth failure that surfaced only as "no usage event observed" instead
of a normal SSE error event).
"""

from __future__ import annotations

from cubebox.agents.schemas import (
    ArtifactEvent,
    ErrorEvent,
    ReasoningEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from cubebox.streams.run_manager import cubepi_dict_to_agent_event

TS = "2026-05-14T00:00:00+00:00"


def test_artifact_dict_maps_to_artifact_event() -> None:
    """Live artifact dict → typed ArtifactEvent (persisted + published).

    Without this branch the dict was dropped, so the artifact store was
    never populated during a live run.
    """
    artifact = {"id": "art_1", "conversation_id": "conv_1", "name": "x", "version": 1}
    evt = cubepi_dict_to_agent_event(
        {"type": "artifact", "action": "created", "artifact": artifact}, TS
    )
    assert isinstance(evt, ArtifactEvent)
    assert evt.data == {"action": "created", "artifact": artifact}


def test_text_delta_dict_maps_to_text_delta_event() -> None:
    evt = cubepi_dict_to_agent_event({"type": "text_delta", "delta": "hi"}, TS)
    assert isinstance(evt, TextDeltaEvent)
    assert evt.data == {"content": "hi", "usage": {}}


def test_reasoning_dict_maps_to_reasoning_event() -> None:
    evt = cubepi_dict_to_agent_event({"type": "reasoning", "delta": "think"}, TS)
    assert isinstance(evt, ReasoningEvent)
    assert evt.data == {"content": "think"}


def test_tool_call_dict_maps_to_tool_call_event() -> None:
    evt = cubepi_dict_to_agent_event(
        {"type": "tool_call", "id": "t1", "name": "calc", "arguments": "{}"}, TS
    )
    assert isinstance(evt, ToolCallEvent)
    assert evt.data == {"tool_call_id": "t1", "name": "calc", "arguments": "{}"}


def test_tool_result_dict_maps_to_tool_result_event() -> None:
    evt = cubepi_dict_to_agent_event(
        {
            "type": "tool_result",
            "tool_call_id": "t1",
            "name": "calc",
            "result": "42",
            "is_error": False,
        },
        TS,
    )
    assert isinstance(evt, ToolResultEvent)
    assert evt.data == {
        "tool_call_id": "t1",
        "name": "calc",
        "content": "42",
        "is_error": False,
        "details": None,
    }


def test_tool_result_dict_propagates_details() -> None:
    """Details (e.g. subagent_events from SubAgentMiddleware's AgentToolResult)
    must survive to the typed event so the frontend gets the live shape that
    matches the post-reload one."""
    evt = cubepi_dict_to_agent_event(
        {
            "type": "tool_result",
            "tool_call_id": "tc-sub",
            "name": "subagent",
            "result": "inner final text",
            "is_error": False,
            "details": {"subagent_events": [{"type": "text_delta", "delta": "hi"}]},
        },
        TS,
    )
    assert isinstance(evt, ToolResultEvent)
    assert evt.data["details"] == {"subagent_events": [{"type": "text_delta", "delta": "hi"}]}


def test_usage_dict_maps_to_usage_event() -> None:
    evt = cubepi_dict_to_agent_event(
        {
            "type": "usage",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 80,
            "cache_write_tokens": 0,
        },
        TS,
    )
    assert isinstance(evt, UsageEvent)
    assert evt.data == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_write_tokens": 0,
    }


def test_error_dict_maps_to_error_event_with_message() -> None:
    """Regression: error dicts must surface as ErrorEvent so SSE consumers
    see a real failure instead of an empty stream. See M5.3 diagnosis."""
    evt = cubepi_dict_to_agent_event({"type": "error", "error": "401 Unauthorized"}, TS)
    assert isinstance(evt, ErrorEvent)
    assert evt.data == {
        "error_code": "run_error",
        "message": "401 Unauthorized",
        "details": "401 Unauthorized",
    }


def test_error_dict_with_missing_message_has_fallback() -> None:
    evt = cubepi_dict_to_agent_event({"type": "error"}, TS)
    assert isinstance(evt, ErrorEvent)
    assert evt.data["message"] == "unknown agent error"


def test_done_dict_returns_none() -> None:
    """``done`` is emitted by the caller with usage data; the cubepi dict
    form is dropped at translation time."""
    assert cubepi_dict_to_agent_event({"type": "done"}, TS) is None


def test_tool_call_delta_dict_returns_none() -> None:
    """``tool_call_delta`` dicts are dropped — the frontend consumes the
    complete ``tool_call`` once toolcall_end arrives."""
    assert cubepi_dict_to_agent_event({"type": "tool_call_delta", "delta": "{"}, TS) is None


def test_unknown_type_returns_none() -> None:
    assert cubepi_dict_to_agent_event({"type": "totally_unknown"}, TS) is None
