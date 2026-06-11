"""Unit tests for the search text extractor."""

from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from cubebox.search.text_extract import extract_searchable_text


def test_user_message_text() -> None:
    msg = UserMessage(content=[TextContent(text="hello")], timestamp=1.0)
    assert extract_searchable_text(msg) == "[user] hello"


def test_assistant_text_strips_reasoning() -> None:
    msg = AssistantMessage(content=[TextContent(text="answer")], timestamp=1.0)
    assert extract_searchable_text(msg) == "[assistant] answer"


def test_tool_result_extracts_text_contents() -> None:
    msg = ToolResultMessage(
        tool_call_id="tc_1",
        tool_name="run",
        content=[TextContent(text="42")],
        timestamp=1.0,
    )
    assert extract_searchable_text(msg) == "[tool_result] 42"


def test_tool_call_is_skipped() -> None:
    msg = AssistantMessage(
        content=[ToolCall(id="tc_1", name="run", arguments={"x": 1})],
        timestamp=1.0,
    )
    assert extract_searchable_text(msg) == ""


def test_empty_text_returns_empty_string() -> None:
    msg = UserMessage(content=[TextContent(text="")], timestamp=1.0)
    assert extract_searchable_text(msg) == ""
