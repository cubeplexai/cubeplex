"""Unit tests for Feishu quoted/replied parent message enrichment."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.im.feishu.connector import FeishuConnector
from cubeplex.im.feishu.long_connection import build_event_handler
from cubeplex.im.types import InboundEvent


def _inbound(
    *,
    text: str = "请解释一下上面这段",
    parent_message_id: str | None = "om_parent_1",
) -> InboundEvent:
    return InboundEvent(
        platform="feishu",
        account_external_id="cli_x",
        platform_event_id="ev1",
        channel_id="oc_g1",
        scope_key="u:on_user",
        scope_kind="participant",
        reply_to_id="om_child_1",
        inbound_message_id="om_child_1",
        sender_ref="on_user",
        sender_open_id="ou_user",
        text=text,
        parent_message_id=parent_message_id,
    )


def _ok_message_resp(
    *,
    msg_type: str = "text",
    content: dict[str, Any] | str | None = None,
    mentions: list[Any] | None = None,
    deleted: bool = False,
    sender_type: str = "user",
    sender_name: str | None = None,
) -> Any:
    raw_content = json.dumps(content) if isinstance(content, dict) else (content or "")
    msg = SimpleNamespace(
        message_id="om_parent_1",
        msg_type=msg_type,
        deleted=deleted,
        body=SimpleNamespace(content=raw_content),
        mentions=mentions or [],
        sender=SimpleNamespace(sender_type=sender_type, sender_name=sender_name),
    )
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = SimpleNamespace(items=[msg])
    return resp


def _err_resp(*, code: int = 99991663, msg: str = "no scope") -> Any:
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = code
    resp.msg = msg
    resp.data = None
    return resp


class TestParseInboundParentId:
    def test_captures_parent_id_when_present(self) -> None:
        raw = {
            "header": {"event_id": "ev_q1", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om_child",
                    "parent_id": "om_parent",
                    "root_id": "om_parent",
                    "chat_id": "oc_g1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "@_user_1 怎么看？"}),
                    "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "Bot"}],
                },
            },
        }
        ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
        assert ev is not None
        assert ev.parent_message_id == "om_parent"
        assert ev.text == "怎么看？"

    def test_parent_id_defaults_to_none_when_absent(self) -> None:
        raw = {
            "header": {"event_id": "ev_q2", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om_child",
                    "chat_id": "oc_dm",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": "hi"}),
                },
            },
        }
        ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
        assert ev is not None
        assert ev.parent_message_id is None


class TestEnrichInboundQuotedMessage:
    @pytest.mark.asyncio
    async def test_prepends_quoted_text_with_mentions(self) -> None:
        client = MagicMock()
        client.im.v1.message.get.return_value = _ok_message_resp(
            msg_type="text",
            content={"text": "@_user_1 @_user_2 帮我看下这个报错\n第二行日志"},
            mentions=[
                SimpleNamespace(key="@_user_1", id="ou_bot", name="Bot"),
                SimpleNamespace(key="@_user_2", id="ou_alice", name="Alice"),
            ],
        )
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        event = _inbound(text="这个怎么修？")

        await connector.enrich_inbound_quoted_message(event)

        client.im.v1.message.get.assert_called_once()
        assert event.text == (
            "[Quoted message]\n> @Alice 帮我看下这个报错\n> 第二行日志\n\n这个怎么修？"
        )

    @pytest.mark.asyncio
    async def test_formats_quoted_post_and_sender_label(self) -> None:
        client = MagicMock()
        client.im.v1.message.get.return_value = _ok_message_resp(
            msg_type="post",
            content={
                "zh_cn": {
                    "title": "发布说明",
                    "content": [
                        [{"tag": "text", "text": "版本 1.2 已上线"}],
                        [{"tag": "img", "image_key": "img_123"}],
                    ],
                }
            },
            sender_name="张三",
        )
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        event = _inbound(text="总结一下")

        await connector.enrich_inbound_quoted_message(event)

        assert event.text == (
            "[Quoted message from 张三]\n"
            "> 发布说明\n"
            "> 版本 1.2 已上线\n"
            "> [image: image.png]\n\n"
            "总结一下"
        )

    @pytest.mark.asyncio
    async def test_formats_quoted_media_and_interactive_and_deleted(self) -> None:
        client = MagicMock()
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)

        client.im.v1.message.get.return_value = _ok_message_resp(
            msg_type="file", content={"file_name": "report.pdf", "file_key": "f1"}
        )
        assert await connector.get_quoted_message_text("om_1") == (
            "[Quoted message]\n> [file: report.pdf]"
        )

        client.im.v1.message.get.return_value = _ok_message_resp(
            msg_type="interactive",
            content={
                "header": {"title": {"tag": "plain_text", "content": "Bot 回答"}},
                "elements": [{"tag": "markdown", "content": "这是卡片正文"}],
            },
            sender_type="app",
        )
        assert await connector.get_quoted_message_text("om_2") == (
            "[Quoted message from bot]\n> Bot 回答\n> 这是卡片正文"
        )

        client.im.v1.message.get.return_value = _ok_message_resp(deleted=True)
        assert await connector.get_quoted_message_text("om_3") == (
            "[Quoted message]\n> [deleted message]"
        )

    @pytest.mark.asyncio
    async def test_noop_without_parent_or_client_and_safe_on_error(self) -> None:
        client = MagicMock()
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)

        ev_no_parent = _inbound(text="hello", parent_message_id=None)
        await connector.enrich_inbound_quoted_message(ev_no_parent)
        assert ev_no_parent.text == "hello"
        client.im.v1.message.get.assert_not_called()

        client.im.v1.message.get.return_value = _err_resp()
        ev_err = _inbound(text="hello")
        await connector.enrich_inbound_quoted_message(ev_err)
        assert ev_err.text == "hello"

        client.im.v1.message.get.side_effect = RuntimeError("network")
        await connector.enrich_inbound_quoted_message(ev_err)
        assert ev_err.text == "hello"

        no_client = FeishuConnector(bot_open_id="ou_bot")
        await no_client.enrich_inbound_quoted_message(ev_err)
        assert ev_err.text == "hello"


@pytest.mark.asyncio
async def test_long_connection_enriches_quoted_message_before_ingest() -> None:
    @asynccontextmanager
    async def _session_ctx() -> Any:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute.return_value = result
        yield session

    captured: list[str] = []
    barrier = asyncio.Event()

    async def fake_ingest(event: InboundEvent, **kwargs: Any) -> Any:
        captured.append(event.text)
        barrier.set()
        return SimpleNamespace(outcome="enqueued")

    outbound_client = MagicMock()
    outbound_client.im.v1.message.get.return_value = _ok_message_resp(
        msg_type="text",
        content={"text": "原消息内容"},
    )
    outbound_client.im.v1.chat.get.return_value = MagicMock(
        success=MagicMock(return_value=False), code=1, msg="skip", data=None
    )

    account = SimpleNamespace(id="acc_test", external_account_id="cli_test")
    handler = build_event_handler(
        account=account,
        bot_open_id="ou_bot",
        ingest=fake_ingest,
        session_maker=_session_ctx,
        loop=asyncio.get_running_loop(),
        run_manager=None,
        redis_key_prefix="test",
        outbound_client=outbound_client,
    )
    p2_map = getattr(handler, "_processorMap", None) or getattr(handler, "_callback_map", {})
    on_message = None
    for k, v in p2_map.items():
        if "im.message.receive_v1" in str(k):
            on_message = getattr(v, "f", v)
            break
    assert on_message is not None

    sdk_event = SimpleNamespace(
        header=SimpleNamespace(
            event_id="ev_lc_q1",
            event_type="im.message.receive_v1",
            token=None,
            create_time=None,
            tenant_key="t1",
            app_id="cli_test",
        ),
        event=SimpleNamespace(
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="ou_user", union_id="on_user", user_id=None),
                sender_type="user",
                tenant_key="t1",
            ),
            message=SimpleNamespace(
                message_id="om_child_lc",
                chat_id="oc_g1",
                chat_type="group",
                message_type="text",
                content=json.dumps({"text": "@_user_1 解释下"}),
                mentions=[
                    SimpleNamespace(
                        key="@_user_1",
                        id=SimpleNamespace(open_id="ou_bot", union_id=None, user_id=None),
                        name="Bot",
                    )
                ],
                root_id="om_parent_1",
                parent_id="om_parent_1",
                thread_id=None,
            ),
        ),
    )

    on_message(sdk_event)
    await asyncio.wait_for(barrier.wait(), timeout=2.0)

    assert captured == ["[Quoted message]\n> 原消息内容\n\n解释下"]
