"""The latest write_todos call inside one assistant message wins."""

from cubeplex.services.history_window import latest_write_todos


def test_last_valid_write_todos_in_one_message_wins() -> None:
    content = [
        {
            "type": "tool_call",
            "name": "write_todos",
            "arguments": {"todos": [{"content": "old step", "status": "in_progress"}]},
        },
        {"type": "text", "text": "working"},
        {
            "type": "tool_call",
            "name": "write_todos",
            "arguments": {
                "todos": [
                    {"content": "old step", "status": "completed"},
                    {"content": "new step", "status": "in_progress"},
                    {"content": "  ", "status": "pending"},
                    {"content": "later", "status": "weird"},
                ]
            },
        },
    ]
    assert latest_write_todos(content) == [
        {"id": None, "description": "old step", "status": "completed"},
        {"id": None, "description": "new step", "status": "in_progress"},
        {"id": None, "description": "later", "status": "pending"},
    ]


def test_malformed_write_todos_does_not_erase_earlier_call() -> None:
    content = [
        {
            "type": "tool_call",
            "name": "write_todos",
            "arguments": {"todos": [{"content": "kept", "status": "pending"}]},
        },
        {"type": "tool_call", "name": "write_todos", "arguments": {"todos": None}},
    ]
    assert latest_write_todos(content) == [
        {"id": None, "description": "kept", "status": "pending"},
    ]


def test_empty_write_todos_clears_the_plan() -> None:
    content = [
        {
            "type": "tool_call",
            "name": "write_todos",
            "arguments": {"todos": [{"content": "gone", "status": "pending"}]},
        },
        {"type": "tool_call", "name": "write_todos", "arguments": {"todos": []}},
    ]
    assert latest_write_todos(content) == []


def test_message_without_valid_write_todos_returns_none() -> None:
    assert latest_write_todos("not-a-list") is None
    assert latest_write_todos([{"type": "text", "text": "hi"}]) is None
    assert (
        latest_write_todos(
            [{"type": "tool_call", "name": "write_todos", "arguments": {"todos": None}}]
        )
        is None
    )
