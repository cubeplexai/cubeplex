"""User-stop marker decision and model-context omission."""

from __future__ import annotations

import pytest
from cubeloop.providers.base import AssistantMessage, TextContent, ToolResultMessage, UserMessage

from cubeplex.agents.user_stop import (
    USER_STOP_KIND,
    omit_user_stop_markers,
    user_stop_message,
    user_stop_needed,
)
from cubeplex.middleware.user_stop import UserStopMiddleware


def _user(run_id: str, text: str = "go") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], run_id=run_id)


def _assistant(
    run_id: str,
    stop_reason: str,
    text: str = "working",
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason=stop_reason,
        run_id=run_id,
    )


def test_marker_is_empty_aborted_assistant() -> None:
    marker = user_stop_message("run-1")
    assert marker.role == "assistant"
    assert marker.content == []
    assert marker.stop_reason == "aborted"
    assert marker.run_id == "run-1"
    assert marker.metadata.get("kind") == USER_STOP_KIND
    assert marker.timestamp is not None


def test_needed_after_tool_use_and_not_after_marker() -> None:
    messages = [_user("run-1"), _assistant("run-1", "tool_use")]
    assert user_stop_needed(messages, "run-1") is True
    assert user_stop_needed([*messages, user_stop_message("run-1")], "run-1") is False


def test_needed_when_the_run_has_no_assistant_yet() -> None:
    assert user_stop_needed([_user("run-1")], "run-1") is True


def test_not_needed_when_the_run_never_appears() -> None:
    older = [_user("run-old"), _assistant("run-old", "stop", "done")]
    assert user_stop_needed(older, "run-new") is False


def test_not_needed_when_last_assistant_already_closed() -> None:
    assert user_stop_needed([_user("run-1"), _assistant("run-1", "aborted")], "run-1") is False
    assert user_stop_needed([_user("run-1"), _assistant("run-1", "error")], "run-1") is False


def test_needed_uses_this_run_not_a_later_one() -> None:
    messages = [
        _user("run-1"),
        _assistant("run-1", "tool_use"),
        _user("run-2"),
        _assistant("run-2", "error", "done"),
    ]
    assert user_stop_needed(messages, "run-1") is True
    assert user_stop_needed(messages, "run-2") is False


def test_tool_result_does_not_hide_an_open_assistant() -> None:
    messages = [
        _user("run-1"),
        _assistant("run-1", "tool_use"),
        ToolResultMessage(
            tool_call_id="tc",
            tool_name="execute",
            content=[TextContent(text="cancelled")],
            run_id="run-1",
            is_error=True,
        ),
    ]
    assert user_stop_needed(messages, "run-1") is True


def test_omit_returns_the_same_list_when_there_is_no_marker() -> None:
    messages = [_user("run-1"), _assistant("run-1", "stop", "hi")]
    assert omit_user_stop_markers(messages) is messages


def test_omit_drops_a_marker_anywhere_and_keeps_the_other_objects() -> None:
    user = _user("run-1")
    marker = user_stop_message("run-1")
    later = _assistant("run-2", "stop", "next")
    omitted = omit_user_stop_markers([user, marker, later])
    assert omitted == [user, later]
    assert omitted[0] is user
    assert omitted[1] is later


@pytest.mark.asyncio
async def test_middleware_strips_markers_before_the_model() -> None:
    user = _user("run-1")
    marker = user_stop_message("run-1")
    result = await UserStopMiddleware().transform_context(
        [user, marker],
        ctx=object(),  # type: ignore[arg-type]
    )
    assert result == [user]
    assert result[0] is user
