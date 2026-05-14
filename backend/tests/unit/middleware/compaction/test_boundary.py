"""Unit tests for safe_boundary — operates on cubepi messages."""

from __future__ import annotations

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from cubebox.middleware.compaction.boundary import safe_boundary


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = []
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content)


def _tool_result(call_id: str, text: str = "ok") -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name="t",
        content=[TextContent(text=text)],
    )


def test_returns_none_when_too_few_messages() -> None:
    msgs: list[Message] = [_user("hi"), _assistant("hello")]
    assert safe_boundary(msgs, keep_recent=4, min_compact=1) is None


def test_returns_boundary_at_human_message_start() -> None:
    msgs: list[Message] = [
        _user("q1"),
        _assistant("a1"),
        _user("q2"),
        _assistant("a2"),
        _user("q3"),
        _assistant("a3"),
    ]
    # keep_recent=2 → candidate idx=4 (UserMessage) ✓
    assert safe_boundary(msgs, keep_recent=2, min_compact=1) == 4


def test_skips_orphan_tool_results_in_suffix() -> None:
    tc = ToolCall(id="c1", name="f", arguments={})
    msgs: list[Message] = [
        _user("q1"),
        _assistant("", [tc]),
        _tool_result("c1"),
        _user("q2"),
        _tool_result("orphan"),  # orphan: no matching tool_call in suffix from idx=3
        _assistant("done"),
    ]
    # candidate=4 not a UserMessage; candidate=3 UserMessage but suffix has orphan tool_result
    # candidate=2 not a UserMessage; candidate=1 not a UserMessage; candidate=0 not allowed (no prefix)
    assert safe_boundary(msgs, keep_recent=2, min_compact=1) is None


def test_min_compact_enforced() -> None:
    msgs: list[Message] = [
        _user("q1"),
        _assistant("a1"),
        _user("q2"),
        _assistant("a2"),
    ]
    # candidate=2 is UserMessage with self-contained suffix; min_compact=3 → None
    assert safe_boundary(msgs, keep_recent=2, min_compact=3) is None
    assert safe_boundary(msgs, keep_recent=2, min_compact=1) == 2
