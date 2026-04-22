"""Tests for stream converter tool_call_delta extraction."""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from cubebox.agents.stream import convert_messages_chunk, convert_updates_chunk

pytestmark = pytest.mark.unit


def test_tool_call_chunk_emits_delta_event() -> None:
    """tool_call_chunks in AIMessageChunk should produce tool_call_delta events."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": "write_file", "args": '{"file_path": "/app/', "id": "tc_1", "index": 0}
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] == "write_file"
    assert deltas[0]["data"]["args_delta"] == '{"file_path": "/app/'
    assert deltas[0]["data"]["tool_call_id"] == "tc_1"
    assert deltas[0]["data"]["index"] == 0


def test_tool_call_chunk_continuation_no_name() -> None:
    """Continuation chunks have name=None and id=None."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[{"name": None, "args": "hello world", "id": None, "index": 0}],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] is None
    assert deltas[0]["data"]["args_delta"] == "hello world"


def test_text_and_tool_call_chunk_coexist() -> None:
    """Text content and tool_call_chunks can appear in the same chunk."""
    msg = AIMessageChunk(
        content="Let me write that file.",
        tool_call_chunks=[],
    )
    events = convert_messages_chunk((msg, {}))
    text_events = [e for e in events if e["type"] == "text_delta"]
    delta_events = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(text_events) == 1
    assert len(delta_events) == 0


def test_empty_args_delta_skipped() -> None:
    """Chunks with empty or None args and no name should not produce events."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": None, "args": "", "id": None, "index": 0},
            {"name": None, "args": None, "id": None, "index": 1},
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 0


def test_name_only_chunk_emits_event() -> None:
    """A chunk with name but empty args should emit (signals tool start)."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": "write_file", "args": "", "id": "tc_1", "index": 0},
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] == "write_file"


def test_system_message_not_emitted_as_text_delta() -> None:
    """SystemMessages injected by middleware must not reach the user as text_delta."""
    msg = SystemMessage(content="Update the todo list before continuing.")
    events = convert_messages_chunk((msg, {}))
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) == 0


def test_convert_updates_chunk_includes_tool_call_started_at() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc_1",
                "name": "write_file",
                "args": {"file_path": "/app/main.py", "content": "print('hi')"},
                "type": "tool_call",
            }
        ],
        response_metadata={"tool_call_started_at_by_index": {"0": "2026-04-10T10:00:00+00:00"}},
    )
    events = convert_updates_chunk({"model": {"messages": [msg]}})
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["data"]["started_at"] == "2026-04-10T10:00:00+00:00"
