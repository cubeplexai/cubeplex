"""Unit tests for DingtalkConnector.parse_inbound."""

from __future__ import annotations

from cubeplex.im.dingtalk.connector import DingtalkConnector
from cubeplex.im.types import DM_SCOPE_KEY


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
            "conversationTitle": "项目协作群",
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
        assert event.channel_name == "项目协作群"

    def test_dm_has_no_channel_name(self) -> None:
        raw = {
            "msgtype": "text",
            "text": {"content": "hello"},
            "msgId": "msg_dm_name",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "conversationTitle": "should-be-ignored",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.scope_kind == "dm"
        assert event.channel_name is None

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

    def test_richtext_dm_message(self) -> None:
        """richText DM messages should be parsed; text extracted from richText items."""
        raw = {
            "msgtype": "richText",
            "content": '{"richText": [{"text": "hello "}, {"text": "world"}]}',
            "msgId": "msg_rt_001",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
            "chatbotUserId": "bot_999",
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "hello world"
        assert event.scope_kind == "dm"

    def test_richtext_group_at_mention(self) -> None:
        """richText group messages with @mention should be parsed and spaces stripped."""
        raw = {
            "msgtype": "richText",
            "content": {"richText": [{"text": " 帮我查一下"}, {"text": "天气"}]},
            "msgId": "msg_rt_002",
            "conversationId": "cid_group_456",
            "conversationType": "2",
            "conversationTitle": "测试群",
            "senderId": "staff_def",
            "senderStaffId": "staff_def",
            "chatbotUserId": "bot_999",
            "isInAtList": True,
        }
        connector = DingtalkConnector(bot_user_id="bot_999")
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "帮我查一下天气"
        assert event.scope_kind == "group"
        assert event.channel_name == "测试群"

    def test_richtext_skips_skill_items(self) -> None:
        """Skill-type items in richText should be excluded from extracted text."""
        raw = {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {"text": "请帮我"},
                    {"text": "执行", "type": "skill", "skillData": {"skillId": "s1"}},
                    {"text": "这个任务"},
                ]
            },
            "msgId": "msg_rt_003",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "请帮我这个任务"

    def test_richtext_old_format(self) -> None:
        """Older richText format uses raw['richText']['richTextList']."""
        raw = {
            "msgtype": "richText",
            "richText": {"richTextList": [{"text": "旧格式消息"}]},
            "msgId": "msg_rt_004",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "旧格式消息"

    def test_richtext_image_downloadcode(self) -> None:
        """Picture items with downloadCode produce an image attachment ref."""
        raw = {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {"text": "看这张图"},
                    {"type": "picture", "downloadCode": "abc123"},
                ]
            },
            "msgId": "msg_rt_img",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == "看这张图"
        assert len(event.attachments) == 1
        att = event.attachments[0]
        assert att.kind == "image"
        assert att.handle == "code:abc123"

    def test_richtext_image_pictureurl_fallback(self) -> None:
        """Picture items with only pictureUrl (no downloadCode) use url: handle."""
        raw = {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {"pictureUrl": "https://cdn.example.com/img.jpg"},
                ]
            },
            "msgId": "msg_rt_pic",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert len(event.attachments) == 1
        att = event.attachments[0]
        assert att.kind == "image"
        assert att.handle == "url:https://cdn.example.com/img.jpg"

    def test_richtext_downloadcode_preferred_over_pictureurl(self) -> None:
        """When an item has both downloadCode and pictureUrl, downloadCode wins."""
        raw = {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {
                        "type": "picture",
                        "downloadCode": "preferred_code",
                        "pictureUrl": "https://cdn.example.com/img.jpg",
                    }
                ]
            },
            "msgId": "msg_rt_both",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert len(event.attachments) == 1
        assert event.attachments[0].handle == "code:preferred_code"

    def test_richtext_file_attachment(self) -> None:
        """File items produce a file attachment ref with the original filename."""
        raw = {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {
                        "type": "file",
                        "downloadCode": "file_code_xyz",
                        "fileName": "report.pdf",
                    }
                ]
            },
            "msgId": "msg_rt_file",
            "conversationId": "cid_dm_123",
            "conversationType": "1",
            "senderId": "staff_abc",
            "senderStaffId": "staff_abc",
        }
        connector = DingtalkConnector()
        event = connector.parse_inbound(raw)
        assert event is not None
        assert event.text == ""
        assert len(event.attachments) == 1
        att = event.attachments[0]
        assert att.kind == "file"
        assert att.filename == "report.pdf"
        assert att.handle == "code:file_code_xyz"

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
