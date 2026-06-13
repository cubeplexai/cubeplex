"""Unit tests for FeishuConnector.parse_inbound (Task 4)."""

import json

from cubebox.im.feishu.connector import FeishuConnector

GROUP_MENTION = {
    "header": {"event_id": "evgrp01", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
            "sender_type": "user",
        },
        "message": {
            "message_id": "om_msg1",
            "chat_id": "oc_chat1",
            "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": '<at user_id="ou_bot">Bot</at> summarize'}),
            "mentions": [
                {
                    "key": "@_user_1",
                    "id": {"open_id": "ou_bot", "union_id": "on_bot"},
                    "name": "Bot",
                }
            ],
        },
    },
}

DM = {
    "header": {"event_id": "evdm01", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
            "sender_type": "user",
        },
        "message": {
            "message_id": "om_msg2",
            "chat_id": "oc_dm1",
            "chat_type": "p2p",
            "message_type": "text",
            "content": json.dumps({"text": "hello"}),
        },
    },
}

BOT_ECHO_BY_TYPE = {
    "header": {"event_id": "evb1", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_bot", "union_id": "on_bot"},
            "sender_type": "app",
        },
        "message": {
            "message_id": "om_msg3",
            "chat_id": "oc_chat1",
            "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": "echo"}),
            "mentions": [],
        },
    },
}

BOT_ECHO_BY_ID = {
    "header": {"event_id": "evb2", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_bot", "union_id": "on_bot"},
            "sender_type": "user",
        },
        "message": {
            "message_id": "om_msg4",
            "chat_id": "oc_chat1",
            "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": '<at user_id="ou_bot">Bot</at> hi'}),
            "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "Bot"}],
        },
    },
}


def test_group_mention_scope_is_per_participant() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(GROUP_MENTION)
    assert ev is not None
    assert ev.account_external_id == ""
    assert ev.platform_event_id == "evgrp01"
    assert ev.channel_id == "oc_chat1"
    assert ev.scope_key == "u:on_user"
    assert ev.scope_kind == "participant"
    assert ev.reply_to_id == "om_msg1"
    assert ev.sender_ref == "on_user"
    assert ev.sender_open_id == "ou_user"
    assert ev.inbound_message_id == "om_msg1"
    assert ev.text == "summarize"


def test_dm_scope_is_chat_level_and_no_reply_target() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(DM)
    assert ev is not None
    assert ev.channel_id == "oc_dm1"
    assert ev.scope_key == "dm"
    assert ev.scope_kind == "dm"
    assert ev.reply_to_id is None
    assert ev.text == "hello"


def test_group_message_without_bot_mention_dropped() -> None:
    """Defense in depth: misconfigured subscription must not cause the bot to
    respond to every group message."""
    raw = {
        "header": {"event_id": "evnomention", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_chatter",
                "chat_id": "oc_chat1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "just chatting, no bot involved"}),
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(raw) is None


def test_bot_echo_by_sender_type_ignored() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(BOT_ECHO_BY_TYPE) is None


def test_bot_echo_by_open_id_ignored() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(BOT_ECHO_BY_ID) is None


def test_non_text_message_ignored() -> None:
    raw = {
        "header": {"event_id": "ev_img", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_dm",
                "chat_type": "p2p",
                "message_type": "image",
                "content": json.dumps({"image_key": "img_key_1"}),
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(raw) is None


def test_wrong_event_type_ignored() -> None:
    raw = {
        "header": {"event_id": "ev_other", "event_type": "im.message.reaction.created_v1"},
        "event": {},
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(raw) is None


def test_empty_text_after_strip_dropped() -> None:
    raw = {
        "header": {"event_id": "ev_empty", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_chat1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": '<at user_id="ou_bot">Bot</at>'}),
                "mentions": [{"id": {"open_id": "ou_bot"}, "name": "Bot"}],
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(raw) is None


def test_union_id_preferred_over_open_id_for_sender_ref() -> None:
    raw = {
        "header": {"event_id": "ev_u", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_dm",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "hi"}),
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(raw)
    assert ev is not None
    assert ev.sender_ref == "on_user"


def test_at_placeholder_substitutes_bot_and_other_users() -> None:
    """Inbound text uses ``@_user_N`` placeholders. The bot's own mention is
    dropped; any other user-mention is rewritten to ``@<name>`` so the LLM
    sees a human-readable name instead of the opaque placeholder."""
    raw = {
        "header": {"event_id": "ev_at", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_chat1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 帮 @_user_2 看看这个问题"}),
                "mentions": [
                    {"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "moltbot"},
                    {"key": "@_user_2", "id": {"open_id": "ou_alice"}, "name": "Alice"},
                ],
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(raw)
    assert ev is not None
    assert ev.text == "帮 @Alice 看看这个问题"


def test_open_id_fallback_when_union_id_missing() -> None:
    raw = {
        "header": {"event_id": "ev_o", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_g",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": '<at user_id="ou_bot">Bot</at> hi'}),
                "mentions": [{"id": {"open_id": "ou_bot"}, "name": "Bot"}],
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(raw)
    assert ev is not None
    assert ev.sender_ref == "ou_user"
    assert ev.scope_key == "u:ou_user"
