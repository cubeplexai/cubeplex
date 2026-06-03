"""Unit tests for the _extract_tool_summaries helper in run_manager."""

from __future__ import annotations

from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from cubebox.streams.run_manager import _extract_tool_summaries


def _user(text: str = "hi") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant_with_calls(*calls: tuple[str, str, dict]) -> AssistantMessage:
    content = [ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls]
    return AssistantMessage(content=content)  # type: ignore[arg-type]


def _result(
    call_id: str, tool_name: str, text: str, *, is_error: bool = False
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name=tool_name,
        content=[TextContent(text=text)],
        is_error=is_error,
    )


def test_empty_messages_returns_empty() -> None:
    assert _extract_tool_summaries([]) == []


def test_no_tool_calls_returns_empty() -> None:
    msgs = [_user("hello"), AssistantMessage(content=[TextContent(text="hi")])]
    assert _extract_tool_summaries(msgs) == []


def test_single_tool_call_ok() -> None:
    msgs = [
        _user("run something"),
        _assistant_with_calls(("tc1", "execute", {"command": "pip install foo"})),
        _result("tc1", "execute", "Successfully installed foo"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) == 1
    assert summaries[0]["name"] == "execute"
    assert "pip install foo" in summaries[0]["args_summary"]
    assert summaries[0]["outcome"] == "ok"


def test_error_result_prefixes_error() -> None:
    msgs = [
        _user("test"),
        _assistant_with_calls(("tc1", "execute", {"command": "twitter whoami"})),
        _result("tc1", "execute", "HTTP 403", is_error=True),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert summaries[0]["outcome"].startswith("error:")
    assert "HTTP 403" in summaries[0]["outcome"]


def test_args_truncated_to_150_chars() -> None:
    long_cmd = "x" * 300
    msgs = [
        _user("run"),
        _assistant_with_calls(("tc1", "execute", {"command": long_cmd})),
        _result("tc1", "execute", "ok"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries[0]["args_summary"]) <= 150


def test_outcome_truncated_to_150_chars() -> None:
    long_output = "y" * 300
    msgs = [
        _user("run"),
        _assistant_with_calls(("tc1", "execute", {"command": "cmd"})),
        _result("tc1", "execute", long_output, is_error=True),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries[0]["outcome"]) <= len("error: ") + 150


def test_capped_at_10_summaries() -> None:
    calls = [(f"tc{i}", "execute", {"command": f"cmd{i}"}) for i in range(15)]
    results = [_result(f"tc{i}", "execute", f"out{i}") for i in range(15)]
    msgs = [_user("run many"), _assistant_with_calls(*calls)] + results
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) <= 10


def test_only_tools_after_last_user_message() -> None:
    msgs = [
        _user("first"),
        _assistant_with_calls(("old_tc", "execute", {"command": "old"})),
        _result("old_tc", "execute", "old result"),
        _user("second"),
        _assistant_with_calls(("new_tc", "execute", {"command": "new"})),
        _result("new_tc", "execute", "new result"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) == 1
    assert "new" in summaries[0]["args_summary"]
