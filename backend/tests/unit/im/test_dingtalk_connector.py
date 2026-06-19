"""Unit tests for DingtalkConnector.parse_inbound."""

from __future__ import annotations

from cubebox.im.dingtalk.connector import DingtalkConnector
from cubebox.im.types import DM_SCOPE_KEY


class TestParseInbound:
    def test_dm_message(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": "hello"},
            "msgId": "msg_001",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.platform == "dingtalk"
        assert event.text == "hello"
        assert event.channel_id == "cid_dm_123"
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
        assert event.sender_ref == "staff_abc"
        assert event.platform_event_id == "msg_001"
        assert event.reply_to_id == "msg_001"

    def test_group_at_mention(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": " what time is it"},
            "msgId": "msg_002",
            "conversationId": "cid_group_456",
            "conversationType": "2",
            "senderId": "staff_def",
            "senderStaffId": "staff_def",
            "chatbotUserId": "bot_999",
            "isInAtList": True,
            "atUsers": [
                {"dingtalkId": "bot_999"},
            ],
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.scope_key == "u:staff_def"
        assert event.scope_kind == "group"
        assert event.text == "what time is it"

    def test_non_text_ignored(self) -> None:
        raw = {
            "msgtype": "image",
            "msgId": "msg_003",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        assert connector.parse_inbound(raw) is None

    def test_strips_at_mention_prefix(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": " hello there"},
            "msgId": "msg_004",
            "conversationId": "cid_group_789",
            "conversationType": "2",
            "senderId": "staff_ghi",
            "senderStaffId": "staff_ghi",
            "chatbotUserId": "bot_999",
            "isInAtList": True,
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "hello there"

    def test_shared_mode_group_uses_channel_scope(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": " hello"},
            "msgId": "msg_005",
            "conversationId": "cid_group_shared",
            "conversationType": "2",
            "senderId": "staff_xyz",
            "senderStaffId": "staff_xyz",
            "chatbotUserId": "bot_999",
            "isInAtList": True,
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw, binding_mode="shared")
        assert event is not None
        assert event.scope_key == "ch"
        assert event.scope_kind == "channel"

    def test_group_non_mention_ignored(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": "random chatter"},
            "msgId": "msg_007",
            "conversationId": "cid_group_456",
            "conversationType": "2",
            "senderId": "staff_def",
            "senderStaffId": "staff_def",
            "chatbotUserId": "bot_999",
            "isInAtList": False,
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        assert connector.parse_inbound(raw) is None

    def test_shared_mode_dm_stays_isolated(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": "hello"},
            "msgId": "msg_006",
            "conversationId": "cid_dm_shared",
            "conversationType": "1",
            "senderId": "staff_xyz",
            "senderStaffId": "staff_xyz",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw, binding_mode="shared")
        assert event is not None
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
