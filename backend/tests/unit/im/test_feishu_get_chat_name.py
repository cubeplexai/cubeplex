"""Unit tests for FeishuConnector.get_chat_name / enrich_inbound_channel_name."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from cubebox.im.feishu.connector import FeishuConnector
from cubebox.im.types import InboundEvent


def _inbound(*, scope_kind: str = "channel", channel_id: str = "oc_g1") -> InboundEvent:
    return InboundEvent(
        platform="feishu",
        account_external_id="cli_x",
        platform_event_id="ev1",
        channel_id=channel_id,
        scope_key="ch",
        scope_kind=scope_kind,
        reply_to_id="m1",
        inbound_message_id="m1",
        sender_ref="ou_x",
        sender_open_id="ou_x",
        text="hi",
    )


def _ok_response(name: str | None) -> Any:
    data = SimpleNamespace(name=name)
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = data
    return resp


def _err_response(*, code: int = 99991663, msg: str = "no scope") -> Any:
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = code
    resp.msg = msg
    resp.data = None
    return resp


class TestGetChatName:
    @pytest.mark.asyncio
    async def test_returns_name_on_success(self) -> None:
        client = MagicMock()
        client.im.v1.chat.get.return_value = _ok_response("研发大群")
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        assert await connector.get_chat_name("oc_g1") == "研发大群"
        client.im.v1.chat.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_client(self) -> None:
        connector = FeishuConnector(bot_open_id="ou_bot")
        assert await connector.get_chat_name("oc_g1") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self) -> None:
        client = MagicMock()
        client.im.v1.chat.get.return_value = _err_response()
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        assert await connector.get_chat_name("oc_g1") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_name(self) -> None:
        client = MagicMock()
        client.im.v1.chat.get.return_value = _ok_response(None)
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        assert await connector.get_chat_name("oc_g1") is None


class TestEnrichInboundChannelName:
    @pytest.mark.asyncio
    async def test_fills_group_channel_name(self) -> None:
        client = MagicMock()
        client.im.v1.chat.get.return_value = _ok_response("项目 Alpha")
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        event = _inbound(scope_kind="channel")
        await connector.enrich_inbound_channel_name(event)
        assert event.channel_name == "项目 Alpha"

    @pytest.mark.asyncio
    async def test_skips_dm(self) -> None:
        client = MagicMock()
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        event = _inbound(scope_kind="dm")
        await connector.enrich_inbound_channel_name(event)
        assert event.channel_name is None
        client.im.v1.chat.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_already_set(self) -> None:
        client = MagicMock()
        connector = FeishuConnector(bot_open_id="ou_bot", client=client)
        event = _inbound(scope_kind="channel")
        event.channel_name = "已有名称"
        await connector.enrich_inbound_channel_name(event)
        assert event.channel_name == "已有名称"
        client.im.v1.chat.get.assert_not_called()
