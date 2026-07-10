"""Tests for the agent-facing historical conversation formatter."""

import pytest

from cubebox.services.conversation_search.history import (
    estimate_tokens,
    format_history_turns,
    format_tool_result,
)

MESSAGES = [
    {
        "seq": 1,
        "role": "user",
        "content": [{"type": "text", "text": "older"}],
    },
    {
        "seq": 2,
        "role": "assistant",
        "content": [{"type": "text", "text": "old answer"}],
    },
    {
        "seq": 3,
        "role": "user",
        "content": [{"type": "text", "text": "newer"}],
    },
    {
        "seq": 4,
        "role": "assistant",
        "content": [
            {"type": "text", "text": "new answer"},
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "search",
                "arguments": {"query": "history", "api_key": "private"},
            },
        ],
    },
    {
        "seq": 5,
        "role": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "search",
        "content": [{"type": "text", "text": "tool result body that is deliberately long"}],
    },
]


def test_history_page_returns_complete_recent_turns_without_result_bodies() -> None:
    page = format_history_turns(MESSAGES, n=5, max_tokens=4_000, before_seq=None)

    assert [turn["user"]["text"] for turn in page.turns] == ["older", "newer"]
    assert page.turns[-1]["tool_calls"][0]["tool_call_id"] == "call-1"
    assert page.turns[-1]["tool_calls"][0]["status"] == "completed"
    assert page.turns[-1]["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    assert "tool result body" not in str(page.turns)


def test_history_page_uses_complete_turns_before_truncating_one_oversized_turn() -> None:
    messages = [
        {
            **message,
            "content": [
                {**block, "text": block["text"] * 100} if block.get("type") == "text" else block
                for block in message.get("content", [])
            ],
        }
        for message in MESSAGES
    ]

    page = format_history_turns(messages, n=5, max_tokens=256, before_seq=None)

    assert len(page.turns) == 1
    assert page.turns[0]["user"]["text"].startswith("newer")
    assert page.truncated is True
    assert page.next_before_seq == 3
    assert page.has_more is True


def test_history_page_bounds_large_non_sensitive_tool_call_arguments() -> None:
    messages = [
        {
            "seq": 1,
            "role": "user",
            "content": [{"type": "text", "text": "Find a record"}],
        },
        {
            "seq": 2,
            "role": "assistant",
            "content": [
                {
                    "type": "tool_call",
                    "id": "call-large-arguments",
                    "name": "search",
                    "arguments": {"query": "x" * 1_000, "api_key": "private"},
                }
            ],
        },
    ]

    page = format_history_turns(messages, n=1, max_tokens=256, before_seq=None)

    arguments = page.turns[0]["tool_calls"][0]["arguments"]
    assert page.truncated is True
    assert page.estimated_tokens <= 256
    assert arguments["api_key"] == "[REDACTED]"
    assert arguments["query"] != "x" * 1_000


def test_history_page_bounds_non_string_tool_call_arguments() -> None:
    messages = [
        {
            "seq": 1,
            "role": "user",
            "content": [{"type": "text", "text": "Find records"}],
        },
        {
            "seq": 2,
            "role": "assistant",
            "content": [
                {
                    "type": "tool_call",
                    "id": "call-non-string-arguments",
                    "name": "search",
                    "arguments": {
                        "record_ids": list(range(1_000)),
                        "filters": {f"enabled_{index}": index % 2 == 0 for index in range(500)},
                        "api_key": "private",
                    },
                }
            ],
        },
        {
            "seq": 3,
            "role": "tool_result",
            "tool_call_id": "call-non-string-arguments",
            "content": [{"type": "text", "text": "never include this result body"}],
        },
    ]

    page = format_history_turns(messages, n=1, max_tokens=256, before_seq=None)

    call = page.turns[0]["tool_calls"][0]
    assert page.truncated is True
    assert page.estimated_tokens <= 256
    assert call["tool_call_id"] == "call-non-string-arguments"
    assert call["name"] == "search"
    assert call["status"] == "completed"
    assert call["arguments"]["api_key"] == "[REDACTED]"
    assert "never include this result body" not in str(page.turns)


def test_formatters_reject_budgets_below_the_capability_minimum() -> None:
    with pytest.raises(ValueError, match="at least 256"):
        format_history_turns(MESSAGES, n=1, max_tokens=255, before_seq=None)

    with pytest.raises(ValueError, match="at least 256"):
        format_tool_result(MESSAGES, tool_call_id="call-1", max_tokens=255)


def test_targeted_tool_result_obeys_the_minimum_token_budget_including_metadata() -> None:
    messages = [
        {
            **MESSAGES[-1],
            "content": [{"type": "text", "text": "tool result body " * 1_000}],
        }
    ]

    result = format_tool_result(messages, tool_call_id="call-1", max_tokens=256)

    assert result is not None
    assert result.tool_call_id == "call-1"
    assert result.truncated is True
    assert (
        estimate_tokens(
            {
                "tool_call_id": result.tool_call_id,
                "tool_name": result.tool_name,
                "content": result.content,
                "is_error": result.is_error,
                "estimated_tokens": result.estimated_tokens,
                "truncated": result.truncated,
            }
        )
        <= 256
    )


def test_long_tool_metadata_uses_a_bounded_reference_that_fetches_the_result() -> None:
    long_call_id = "call-" + "i" * 10_000
    long_tool_name = "tool-" + "n" * 10_000
    messages = [
        {
            "seq": 1,
            "role": "user",
            "content": [{"type": "text", "text": "Find the record"}],
        },
        {
            "seq": 2,
            "role": "assistant",
            "content": [
                {
                    "type": "tool_call",
                    "id": long_call_id,
                    "name": long_tool_name,
                    "arguments": {},
                }
            ],
        },
        {
            "seq": 3,
            "role": "tool_result",
            "tool_call_id": long_call_id,
            "tool_name": long_tool_name,
            "content": [{"type": "text", "text": "exact persisted result"}],
        },
    ]

    page = format_history_turns(messages, n=1, max_tokens=256, before_seq=None)

    call = page.turns[0]["tool_calls"][0]
    assert page.estimated_tokens <= 256
    assert call["tool_call_id"] != long_call_id
    assert len(call["tool_call_id"]) < 100
    assert call["name"] != long_tool_name
    result = format_tool_result(messages, tool_call_id=call["tool_call_id"], max_tokens=256)
    assert result is not None
    assert result.content == "exact persisted result"
    assert result.tool_call_id == call["tool_call_id"]
    assert result.tool_name != long_tool_name
    assert result.estimated_tokens <= 256


def test_targeted_tool_result_returns_none_for_an_unknown_call() -> None:
    assert format_tool_result(MESSAGES, tool_call_id="missing", max_tokens=256) is None
