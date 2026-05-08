"""Tests for approx_tokens — should count tokens across all message types."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cubebox.middleware.compaction.tokens import approx_tokens


def test_empty_messages_zero():
    assert approx_tokens([]) == 0


def test_counts_text_content():
    msgs = [HumanMessage(content="hello world"), AIMessage(content="hi there")]
    n = approx_tokens(msgs)
    assert n > 0
    assert n < 50


def test_counts_tool_message_content():
    msgs = [ToolMessage(content="big tool output " * 100, tool_call_id="t1")]
    assert approx_tokens(msgs) > 100


def test_counts_system_message():
    assert approx_tokens([SystemMessage(content="you are a helpful assistant")]) > 0


def test_handles_list_content_blocks():
    msg = HumanMessage(content=[{"type": "text", "text": "block one"}])
    assert approx_tokens([msg]) > 0
