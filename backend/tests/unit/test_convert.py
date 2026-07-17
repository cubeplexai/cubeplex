"""Tests for the request-body → cubepi.UserMessage builder.

Outbound wire conversion was removed when cubeplex aligned to cubepi's
native message shape; only the request side still has a meaningful DTO
translation (text + attachment ids → UserMessage with file_attachment
metadata + memory_snapshot).
"""

from cubepi.providers.base import UserMessage

from cubeplex.agents.convert import wire_input_to_cubepi_user_message


def test_wire_input_to_user_message_simple_text() -> None:
    msg = wire_input_to_cubepi_user_message("hello world")
    assert isinstance(msg, UserMessage)
    assert msg.content[0].text == "hello world"


def test_wire_input_carries_attachments_in_metadata() -> None:
    attachments = [
        {"kind": "image", "filename": "a.png", "size_bytes": 100, "sandbox_path": "/x/a.png"}
    ]
    msg = wire_input_to_cubepi_user_message("look at this", attachments=attachments)
    assert msg.content[0].text == "look at this"
    assert msg.metadata["attachments"] == attachments


def test_wire_input_carries_memory_snapshot() -> None:
    snap = {"id": "m1", "items": []}
    msg = wire_input_to_cubepi_user_message("hi", memory_snapshot=snap)
    assert msg.metadata["memory_snapshot"] == snap
