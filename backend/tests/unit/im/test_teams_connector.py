from cubeplex.im.teams.connector import TeamsConnector
from cubeplex.im.types import (
    DM_SCOPE_KEY,
    make_participant_scope,
    make_thread_participant_scope,
)


def _make_activity(
    *,
    conversation_type: str = "personal",
    text: str = "hello",
    from_aad: str = "aad-user-123",
    from_id: str = "29:user-id",
    from_name: str = "Test User",
    conversation_id: str = "conv-123",
    message_id: str = "msg-001",
    reply_to_id: str | None = None,
    recipient_id: str = "bot-app-id",
    at_mention: bool = False,
) -> dict:
    activity: dict = {
        "type": "message",
        "id": message_id,
        "text": text,
        "from": {
            "id": from_id,
            "aadObjectId": from_aad,
            "name": from_name,
        },
        "conversation": {
            "id": conversation_id,
            "conversationType": conversation_type,
        },
        "recipient": {
            "id": recipient_id,
            "name": "CubeBot",
        },
    }
    if reply_to_id:
        activity["replyToId"] = reply_to_id
    if at_mention:
        activity["entities"] = [
            {
                "type": "mention",
                "mentioned": {"id": recipient_id, "name": "CubeBot"},
                "text": "<at>CubeBot</at>",
            }
        ]
        activity["text"] = f"<at>CubeBot</at> {text}"
    return activity


class TestParseInbound:
    def test_dm_returns_event(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity())
        assert ev is not None
        assert ev.platform == "teams"
        assert ev.scope_key == DM_SCOPE_KEY
        assert ev.scope_kind == "dm"
        assert ev.text == "hello"
        assert ev.sender_ref == "aad-user-123"
        assert ev.sender_open_id == "aad-user-123"
        assert ev.reply_to_id is None

    def test_group_chat_with_mention(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity(conversation_type="groupChat", at_mention=True))
        assert ev is not None
        assert ev.scope_key == make_participant_scope("aad-user-123")
        assert ev.scope_kind == "group"
        assert ev.text == "hello"

    def test_channel_with_mention(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity(conversation_type="channel", at_mention=True))
        assert ev is not None
        assert ev.scope_key == make_participant_scope("aad-user-123")
        assert ev.scope_kind == "channel"

    def test_channel_thread_reply(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(
                conversation_type="channel",
                at_mention=True,
                reply_to_id="parent-msg-id",
            )
        )
        assert ev is not None
        assert ev.scope_key == make_thread_participant_scope("aad-user-123", "parent-msg-id")
        assert ev.scope_kind == "thread"

    def test_group_without_mention_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity(conversation_type="groupChat", at_mention=False))
        assert ev is None

    def test_non_message_type_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        activity = _make_activity()
        activity["type"] = "conversationUpdate"
        assert c.parse_inbound(activity) is None

    def test_bot_own_message_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(_make_activity(from_id="bot-app-id", from_aad=""))
        assert ev is None

    def test_empty_text_after_strip_ignored(self) -> None:
        c = TeamsConnector(bot_id="bot-app-id")
        ev = c.parse_inbound(
            _make_activity(
                conversation_type="groupChat",
                at_mention=True,
                text="",
            )
        )
        assert ev is None
