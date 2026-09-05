from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubeplex.im.wecom.connector import WecomConnector


def _frame(
    *,
    msgid: str = "msg-1",
    req_id: str = "req-1",
    user_id: str = "user-1",
    chat_id: str | None = None,
    chat_type: str = "single",
    msgtype: str = "text",
    content: str = "hello",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "msgid": msgid,
        "chattype": chat_type,
        "from": {"userid": user_id},
        "msgtype": msgtype,
        msgtype: {"content": content},
    }
    if chat_id is not None:
        body["chatid"] = chat_id
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": req_id},
        "body": body,
    }


def test_parse_dm_preserves_receipt_and_reply_roles() -> None:
    event = WecomConnector(bot_id="bot-1").parse_inbound(_frame())

    assert event is not None
    assert event.platform == "wecom"
    assert event.account_external_id == "bot-1"
    assert event.platform_event_id == "msg-1"
    assert event.inbound_message_id == "msg-1"
    assert event.reply_to_id == "req-1"
    assert event.channel_id == "user-1"
    assert event.scope_key == "dm"
    assert event.scope_kind == "dm"
    assert event.sender_ref == "user-1"
    assert event.sender_open_id == "user-1"
    assert event.text == "hello"


@pytest.mark.parametrize(
    ("binding_mode", "scope_key"),
    [("isolated", "u:user-1"), ("shared", "ch")],
)
def test_parse_group_scope_and_leading_mention(
    binding_mode: str,
    scope_key: str,
) -> None:
    raw = _frame(chat_id="group-1", chat_type="group", content="@CubePlex  hello")

    event = WecomConnector().parse_inbound(raw, binding_mode=binding_mode)  # type: ignore[arg-type]

    assert event is not None
    assert event.channel_id == "group-1"
    assert event.scope_key == scope_key
    assert event.scope_kind == ("group" if binding_mode == "isolated" else "channel")
    assert event.text == "hello"


def test_parse_mixed_voice_and_quote_fallback() -> None:
    connector = WecomConnector()
    mixed = _frame(msgtype="mixed", content="")
    mixed["body"]["mixed"] = {
        "msg_item": [
            {"msgtype": "text", "text": {"content": "first"}},
            {"msgtype": "image", "image": {"url": "ignored"}},
            {"msgtype": "text", "text": {"content": "second"}},
        ]
    }
    voice = _frame(msgtype="voice", content="transcript")
    quote = _frame(msgtype="image", content="")
    quote["body"]["quote"] = {"msgtype": "text", "text": {"content": "quoted"}}

    assert connector.parse_inbound(mixed).text == "first\nsecond"  # type: ignore[union-attr]
    assert connector.parse_inbound(voice).text == "transcript"  # type: ignore[union-attr]
    assert connector.parse_inbound(quote).text == "quoted"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "raw",
    [
        {"cmd": "other", "headers": {"req_id": "req"}, "body": {}},
        _frame(msgid="", content="hello"),
        _frame(user_id="", content="hello"),
        _frame(req_id="", content="hello"),
        _frame(msgtype="image", content=""),
        _frame(user_id="bot-1", content="echo"),
    ],
)
def test_parse_drops_unsupported_or_malformed_callbacks(raw: dict[str, Any]) -> None:
    assert WecomConnector(bot_id="bot-1").parse_inbound(raw) is None


@pytest.mark.asyncio
async def test_send_to_chat_prefers_passive_and_chunks_proactive() -> None:
    gateway = AsyncMock()
    gateway.send_passive.return_value = {"errcode": 0, "errmsg": "ok"}
    gateway.send_proactive.return_value = {"headers": {"req_id": "sent-1"}}
    connector = WecomConnector(gateway=gateway)

    passive_id = await connector.send_to_chat("chat-1", "reply-1", "hello")
    proactive_id = await connector.send_to_chat("chat-1", None, "a" * 8001)

    assert passive_id == "reply-1"
    gateway.send_passive.assert_awaited_once_with(
        "reply-1",
        {"msgtype": "markdown", "markdown": {"content": "hello"}},
        final=True,
        skip_if_pending=False,
    )
    assert gateway.send_proactive.await_count == 3
    assert [
        len(call.args[1]["markdown"]["content"]) for call in gateway.send_proactive.await_args_list
    ] == [4000, 4000, 1]
    assert proactive_id == "sent-1"


@pytest.mark.asyncio
async def test_native_file_methods_are_explicitly_unsupported() -> None:
    connector = WecomConnector()

    assert await connector.upload_image("/tmp/image.png") is None
    assert not await connector.send_image(local_path="/tmp/image.png", filename="image.png")
    assert not await connector.send_file(
        local_path="/tmp/file.txt",
        filename="file.txt",
        mime="text/plain",
    )
