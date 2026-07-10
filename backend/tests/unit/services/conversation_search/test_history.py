"""Tests for the agent-facing historical conversation formatter."""

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
    page = format_history_turns(MESSAGES, n=5, max_tokens=12, before_seq=None)

    assert [turn["user"]["text"] for turn in page.turns] == ["newer"]
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

    page = format_history_turns(messages, n=1, max_tokens=100, before_seq=None)

    arguments = page.turns[0]["tool_calls"][0]["arguments"]
    assert page.truncated is True
    assert page.estimated_tokens <= 100
    assert arguments["api_key"] == "[REDACTED]"
    assert arguments["query"] != "x" * 1_000


def test_targeted_tool_result_obeys_its_token_budget() -> None:
    result = format_tool_result(MESSAGES, tool_call_id="call-1", max_tokens=35)

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
                "truncated": result.truncated,
            }
        )
        <= 35
    )


def test_targeted_tool_result_returns_none_for_an_unknown_call() -> None:
    assert format_tool_result(MESSAGES, tool_call_id="missing", max_tokens=100) is None
