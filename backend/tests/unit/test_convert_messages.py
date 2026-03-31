from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cubebox.agents.convert import convert_to_api_messages


def test_convert_human_message():
    msgs = [HumanMessage(content="Hello")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Hello"


def test_convert_ai_message_text():
    msgs = [AIMessage(content="Hi there")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Hi there"
    assert result[0]["tool_calls"] is None


def test_convert_ai_message_with_tool_calls():
    msg = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "execute", "args": {"command": "ls"}, "type": "tool_call"}],
    )
    result = convert_to_api_messages([msg])
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] is None
    assert result[0]["tool_calls"] == [{"name": "execute", "arguments": {"command": "ls"}}]


def test_convert_tool_message():
    msgs = [ToolMessage(content="file.txt\nother.txt", name="execute", tool_call_id="1")]
    result = convert_to_api_messages(msgs)
    assert result[0]["role"] == "tool"
    assert result[0]["name"] == "execute"
    assert result[0]["content"] == "file.txt\nother.txt"


def test_convert_ai_message_with_reasoning():
    msg = AIMessage(
        content="The answer is 4",
        additional_kwargs={"reasoning_content": "2+2=4"},
    )
    result = convert_to_api_messages([msg])
    assert result[0]["reasoning"] == "2+2=4"


def test_convert_mixed_messages():
    msgs = [
        HumanMessage(content="What is 2+2?"),
        AIMessage(content="The answer is 4"),
    ]
    result = convert_to_api_messages(msgs)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
