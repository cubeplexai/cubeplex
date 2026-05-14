"""convert_pi tests — cubepi.Message ↔ cubebox wire format (M1.2)."""

from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from cubebox.agents.convert import (
    cubepi_message_to_wire,
    wire_input_to_cubepi_user_message,
)


def test_user_message_to_wire_text_only() -> None:
    msg = UserMessage(content=[TextContent(text="hello")])
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "user"
    assert out["content"] == "hello"


def test_assistant_message_to_wire_text_only() -> None:
    msg = AssistantMessage(content=[TextContent(text="hi back")], usage=Usage())
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "assistant"
    assert out["content"] == "hi back"


def test_assistant_message_to_wire_with_tool_call() -> None:
    """tool_calls land in metadata.tool_calls as cubebox-shaped dicts."""
    tc = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    msg = AssistantMessage(
        content=[TextContent(text="calling tool"), tc],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "assistant"
    assert out["content"] == "calling tool"
    assert out["metadata"]["tool_calls"] == [
        {"id": "tc1", "name": "search", "arguments": {"q": "x"}}
    ]
    assert out["metadata"]["usage"]["input_tokens"] == 10
    assert out["metadata"]["usage"]["output_tokens"] == 5


def test_tool_result_message_to_wire() -> None:
    msg = ToolResultMessage(
        content=[TextContent(text="result text")],
        tool_call_id="tc1",
        tool_name="search",
    )
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "tool"
    assert out["content"] == "result text"
    assert out["metadata"]["tool_call_id"] == "tc1"
    assert out["metadata"]["tool_name"] == "search"


def test_wire_input_to_user_message_simple_text() -> None:
    """API request body's user-input text → cubepi.UserMessage."""
    msg = wire_input_to_cubepi_user_message("hello world")
    assert isinstance(msg, UserMessage)
    assert msg.content[0].text == "hello world"


def test_wire_input_carries_attachments_in_metadata() -> None:
    """Attachment blocks (file_attachment dicts) land in metadata for M3 to render."""
    attachments = [
        {"kind": "image", "filename": "a.png", "size_bytes": 100, "sandbox_path": "/x/a.png"}
    ]
    msg = wire_input_to_cubepi_user_message("look at this", attachments=attachments)
    assert msg.content[0].text == "look at this"
    assert msg.metadata["attachments"] == attachments


def test_user_message_passthrough_preserves_metadata() -> None:
    """If a UserMessage has metadata (memory snapshots etc.), to_wire preserves it."""
    msg = UserMessage(
        content=[TextContent(text="hi")],
        metadata={"memory_snapshot": {"id": "m1"}},
    )
    out = cubepi_message_to_wire(msg)
    assert out["metadata"]["memory_snapshot"] == {"id": "m1"}
