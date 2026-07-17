"""CubeplexCacheMarkerPolicy tests (M1.1)."""

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from cubeplex.llm.cache_markers import CubeplexCacheMarkerPolicy


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant(text: str = "ok", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = [TextContent(text=text)]
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, usage=Usage())


def _tool_result(tool_call_id: str, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        content=[TextContent(text=text)],
        tool_call_id=tool_call_id,
        tool_name="t",
    )


def test_policy_marks_system_and_tools() -> None:
    p = CubeplexCacheMarkerPolicy()
    assert p.mark_system() is True
    assert p.mark_last_tool() is True


def test_indices_empty_list() -> None:
    p = CubeplexCacheMarkerPolicy()
    assert p.message_breakpoint_indices([]) == []


def test_indices_only_user_message_no_assistant_yet() -> None:
    """First turn before any model response: no AssistantMessage → no breakpoint."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [_user("hi")]
    assert p.message_breakpoint_indices(msgs) == []


def test_indices_picks_last_assistant() -> None:
    """[user, assistant, user] → mark index 1 (the assistant)."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [_user("a"), _assistant("b"), _user("c")]
    assert p.message_breakpoint_indices(msgs) == [1]


def test_indices_picks_most_recent_assistant() -> None:
    """[user, assistant, user, assistant, user] → mark index 3."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [
        _user("a"),
        _assistant("b"),
        _user("c"),
        _assistant("d"),
        _user("e"),
    ]
    assert p.message_breakpoint_indices(msgs) == [3]


def test_indices_skips_user_and_tool_result() -> None:
    """[user, assistant(tool_call), tool_result, assistant, user] → mark index 3."""
    p = CubeplexCacheMarkerPolicy()
    tc = ToolCall(id="tc1", name="t", arguments={})
    msgs: list[Message] = [
        _user("a"),
        _assistant("calling tool", tool_calls=[tc]),
        _tool_result("tc1", "result"),
        _assistant("done"),
        _user("next"),
    ]
    assert p.message_breakpoint_indices(msgs) == [3]
