from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubeplex.im.slack.connector import SlackConnector
from cubeplex.im.types import DM_SCOPE_KEY


def _make_event(
    *,
    text: str = "hello bot",
    user: str = "U111",
    channel: str = "C222",
    ts: str = "1234567890.123456",
    thread_ts: str = "",
    channel_type: str = "channel",
    event_type: str = "app_mention",
    subtype: str = "",
    bot_id: str = "",
    client_msg_id: str = "msg-001",
) -> dict[str, str]:
    raw: dict[str, str] = {
        "type": event_type,
        "text": text,
        "user": user,
        "channel": channel,
        "ts": ts,
        "channel_type": channel_type,
    }
    if thread_ts:
        raw["thread_ts"] = thread_ts
    if subtype:
        raw["subtype"] = subtype
    if bot_id:
        raw["bot_id"] = bot_id
    if client_msg_id:
        raw["client_msg_id"] = client_msg_id
    return raw


class TestSlackConnectorParseInbound:
    def setup_method(self) -> None:
        self.connector = SlackConnector(bot_user_id="UBOT")

    def test_dm_message(self) -> None:
        raw = _make_event(
            channel_type="im",
            event_type="message",
        )
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.platform == "slack"
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
        assert event.text == "hello bot"
        assert event.reply_to_id is None
        assert event.sender_ref == "U111"
        assert event.sender_open_id == "U111"

    def test_channel_mention(self) -> None:
        raw = _make_event(
            text="<@UBOT> what is 2+2?",
            event_type="app_mention",
        )
        event = self.connector.parse_inbound(raw)
        assert event is not None
        # Root channel @bot is a thread root keyed by its own ts so follow-up
        # replies in the opened Slack thread share the same conversation.
        assert event.scope_key == "u:U111|t:1234567890.123456"
        assert event.scope_kind == "thread"
        assert event.text == "what is 2+2?"
        assert event.reply_to_id == "1234567890.123456"

    def test_thread_mention(self) -> None:
        raw = _make_event(
            text="<@UBOT> explain more",
            event_type="app_mention",
            thread_ts="1234567890.000000",
            ts="1234567891.111111",
        )
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.scope_key == "u:U111|t:1234567890.000000"
        assert event.scope_kind == "thread"
        assert event.reply_to_id == "1234567890.000000"
        assert event.text == "explain more"

    def test_bot_message_ignored(self) -> None:
        raw = _make_event(user="UBOT")
        event = self.connector.parse_inbound(raw)
        assert event is None

    def test_other_bot_message_ignored(self) -> None:
        raw = _make_event(bot_id="B999")
        event = self.connector.parse_inbound(raw)
        assert event is None

    def test_subtype_message_ignored(self) -> None:
        raw = _make_event(subtype="message_changed")
        event = self.connector.parse_inbound(raw)
        assert event is None

    def test_empty_text_after_mention_strip_ignored(self) -> None:
        raw = _make_event(text="<@UBOT>", event_type="app_mention")
        event = self.connector.parse_inbound(raw)
        assert event is None

    def test_platform_event_id_uses_client_msg_id(self) -> None:
        raw = _make_event(client_msg_id="custom-id-123")
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.platform_event_id == "custom-id-123"

    def test_platform_event_id_fallback(self) -> None:
        raw = _make_event(client_msg_id="")
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.platform_event_id == "C222:1234567890.123456"

    def test_bot_mention_stripped_from_text(self) -> None:
        raw = _make_event(
            text="<@UBOT> hello there",
            event_type="app_mention",
        )
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "hello there"

    def test_inbound_message_id_is_ts(self) -> None:
        raw = _make_event(ts="9999.1111")
        event = self.connector.parse_inbound(raw)
        assert event is not None
        assert event.inbound_message_id == "9999.1111"


class TestSlackConnectorLifecycleHooks:
    """Hourglass is added at tailer start (on_processing_start), matching
    Feishu/Discord timing — not delayed until first streamed content."""

    @pytest.mark.asyncio
    async def test_processing_start_adds_hourglass(self) -> None:
        from unittest.mock import AsyncMock

        c = SlackConnector(bot_user_id="UBOT", channel_id="C1")
        c.add_reaction = AsyncMock(return_value=True)  # type: ignore[method-assign]
        c.remove_reaction = AsyncMock(return_value=True)  # type: ignore[method-assign]
        state = SimpleNamespace(inbound_message_id="1234.5678")
        await c.on_processing_start(state)
        c.add_reaction.assert_awaited_once_with("1234.5678", "hourglass_flowing_sand")
        await c.on_processing_complete(state)
        c.remove_reaction.assert_awaited_once_with("1234.5678", "hourglass_flowing_sand")
        c.remove_reaction.reset_mock()
        await c.on_processing_failed(state)
        c.remove_reaction.assert_awaited_once_with("1234.5678", "hourglass_flowing_sand")

    @pytest.mark.asyncio
    async def test_processing_start_skips_without_inbound_id(self) -> None:
        from unittest.mock import AsyncMock

        c = SlackConnector(bot_user_id="UBOT", channel_id="C1")
        c.add_reaction = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await c.on_processing_start(SimpleNamespace(inbound_message_id=None))
        c.add_reaction.assert_not_awaited()


class TestSlackReactionIdempotency:
    """finalize + on_processing_complete both clear hourglass — second
    remove must not log a hard failure."""

    @pytest.mark.asyncio
    async def test_remove_reaction_no_reaction_is_success(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        class FakeSlackApiError(Exception):
            def __init__(self) -> None:
                super().__init__("no_reaction")
                self.response = {"error": "no_reaction"}

        client = MagicMock()
        client.reactions_remove = AsyncMock(side_effect=FakeSlackApiError())
        c = SlackConnector(bot_user_id="UBOT", client=client, channel_id="C1")
        assert await c.remove_reaction("1234.5678", "hourglass_flowing_sand") is True

    @pytest.mark.asyncio
    async def test_add_reaction_already_reacted_is_success(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        class FakeSlackApiError(Exception):
            def __init__(self) -> None:
                super().__init__("already_reacted")
                self.response = {"error": "already_reacted"}

        client = MagicMock()
        client.reactions_add = AsyncMock(side_effect=FakeSlackApiError())
        c = SlackConnector(bot_user_id="UBOT", client=client, channel_id="C1")
        assert await c.add_reaction("1234.5678", "hourglass_flowing_sand") is True
