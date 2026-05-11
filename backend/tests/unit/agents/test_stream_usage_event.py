"""convert_messages_chunk emits a UsageEvent when the chunk carries usage_metadata."""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from cubebox.agents.stream import convert_messages_chunk


def _wrap(msg: AIMessageChunk) -> tuple[AIMessageChunk, dict]:
    return (msg, {"langgraph_node": "agent"})


def test_no_usage_event_when_metadata_absent() -> None:
    chunk = AIMessageChunk(content="hi", response_metadata={})
    events = convert_messages_chunk(_wrap(chunk))
    assert all(e["type"] != "usage" for e in events)


def test_emits_usage_event_when_metadata_present_with_cache() -> None:
    chunk = AIMessageChunk(
        content="hi",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            # Anthropic reports BOTH cache_read AND cache_creation under
            # input_token_details (cache_creation_input_tokens lives on the
            # input side per the API). LangChain preserves this layout.
            "input_token_details": {"cache_read": 80, "cache_creation": 15},
            "output_token_details": {},
        },
    )
    events = convert_messages_chunk(_wrap(chunk))
    usage_events = [e for e in events if e["type"] == "usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["data"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_write_tokens": 15,
    }


def test_cache_write_does_not_read_from_output_token_details() -> None:
    """Regression: cache_creation lives on the input side, not output. If a
    future refactor moves the source back to output_token_details, this
    test fails fast."""
    chunk = AIMessageChunk(
        content="hi",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_creation": 42},
            # If the impl wrongly reads from output_token_details, it
            # would pick up the bogus value below.
            "output_token_details": {"cache_write": 9999},
        },
    )
    events = convert_messages_chunk(_wrap(chunk))
    usage_events = [e for e in events if e["type"] == "usage"]
    assert usage_events[0]["data"]["cache_write_tokens"] == 42


def test_no_usage_event_when_input_tokens_zero() -> None:
    """Intermediate streamed chunks have usage_metadata={input_tokens:0, ...};
    only the final chunk in a turn (with non-zero totals) should emit."""
    chunk = AIMessageChunk(
        content="hi",
        usage_metadata={
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_token_details": {},
            "output_token_details": {},
        },
    )
    events = convert_messages_chunk(_wrap(chunk))
    assert all(e["type"] != "usage" for e in events)


def test_dicts_to_sse_events_handles_usage_type() -> None:
    """Cover the dispatch path; UsageEvent must serialize through the SSE layer."""
    from cubebox.streams.run_manager import _dicts_to_sse_events

    events = _dicts_to_sse_events(
        [
            {
                "type": "usage",
                "timestamp": "2026-05-09T00:00:00Z",
                "data": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 80,
                    "cache_write_tokens": 0,
                },
                "agent_id": None,
            }
        ],
        {},
    )
    assert len(events) == 1
    assert events[0].type == "usage"
    assert events[0].data["cache_read_tokens"] == 80
