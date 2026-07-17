"""Unit tests for FeishuConnector.parse_inbound (Task 4)."""

import json

from cubeplex.im.feishu.connector import FeishuConnector

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


def test_image_message_parsed_as_attachment() -> None:
    # Media messages are no longer dropped — a DM image becomes an InboundEvent
    # carrying an image attachment ref (resolved to bytes later by the worker).
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
    ev = c.parse_inbound(raw)
    assert ev is not None
    assert ev.text == ""
    assert len(ev.attachments) == 1
    assert ev.attachments[0].kind == "image"
    assert ev.attachments[0].handle == "img_key_1"


def test_file_message_parsed_as_attachment() -> None:
    raw = {
        "header": {"event_id": "ev_file", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_dm",
                "chat_type": "p2p",
                "message_type": "file",
                "content": json.dumps({"file_key": "file_key_1", "file_name": "r.pdf"}),
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(raw)
    assert ev is not None
    assert ev.attachments[0].kind == "file"
    assert ev.attachments[0].handle == "file_key_1"
    assert ev.attachments[0].filename == "r.pdf"


def test_unsupported_message_type_still_ignored() -> None:
    # A type outside text + the media set (e.g. sticker) is still dropped.
    raw = {
        "header": {"event_id": "ev_st", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_dm",
                "chat_type": "p2p",
                "message_type": "sticker",
                "content": json.dumps({"file_key": "sticker_1"}),
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


def _post_event(content: dict, *, chat_type: str = "p2p", mentions: list | None = None) -> dict:
    msg: dict = {
        "message_id": "om_post",
        "chat_id": "oc_dm",
        "chat_type": chat_type,
        "message_type": "post",
        "content": json.dumps(content),
    }
    if mentions is not None:
        msg["mentions"] = mentions
    return {
        "header": {"event_id": "ev_post", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                "sender_type": "user",
            },
            "message": msg,
        },
    }


def test_post_mixes_text_and_image() -> None:
    # The headline case: a rich-text post with text + an embedded image must
    # yield BOTH the text and an image attachment — previously the whole
    # message (text included) was dropped.
    raw = _post_event(
        {
            "title": "周报",
            "content": [
                [{"tag": "text", "text": "这是本周进展："}],
                [{"tag": "img", "image_key": "img_v2_abc"}],
                [{"tag": "text", "text": "请查收。"}],
            ],
        }
    )
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert "周报" in ev.text and "本周进展" in ev.text and "请查收" in ev.text
    assert len(ev.attachments) == 1
    assert ev.attachments[0].kind == "image"
    assert ev.attachments[0].handle == "img_v2_abc"


def test_post_renders_links_and_files() -> None:
    raw = _post_event(
        {
            "content": [
                [
                    {"tag": "text", "text": "见文档 "},
                    {"tag": "a", "text": "设计稿", "href": "https://x.test/d"},
                ],
                [{"tag": "media", "file_key": "file_v3_1", "file_name": "spec.pdf"}],
            ]
        }
    )
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert "[设计稿](https://x.test/d)" in ev.text
    assert len(ev.attachments) == 1
    assert ev.attachments[0].handle == "file_v3_1"
    assert ev.attachments[0].filename == "spec.pdf"


def test_post_image_only_no_text_still_parsed() -> None:
    raw = _post_event({"content": [[{"tag": "img", "image_key": "img_only"}]]})
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert ev.text == ""
    assert len(ev.attachments) == 1


def test_post_group_drops_bot_mention_and_passes_gate() -> None:
    # A group post that @-mentions the bot passes the group gate, and the bot's
    # own @ is stripped from the reconstructed text.
    raw = _post_event(
        {
            "content": [
                [
                    {"tag": "at", "user_id": "@_user_1"},
                    {"tag": "text", "text": " 看下这个"},
                    {"tag": "img", "image_key": "img_grp"},
                ]
            ]
        },
        chat_type="group",
        mentions=[{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "Bot"}],
    )
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert "@Bot" not in ev.text and "看下这个" in ev.text
    assert len(ev.attachments) == 1


def test_post_language_wrapped_payload_unwrapped() -> None:
    raw = _post_event(
        {"zh_cn": {"title": "T", "content": [[{"tag": "text", "text": "wrapped body"}]]}}
    )
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert "wrapped body" in ev.text


def test_post_group_without_bot_mention_dropped() -> None:
    # Defense-in-depth: a group post that does NOT @ the bot must be dropped,
    # exactly like a group text message.
    raw = _post_event(
        {"content": [[{"tag": "text", "text": "闲聊一下"}, {"tag": "img", "image_key": "x"}]]},
        chat_type="group",
        mentions=[],
    )
    assert FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw) is None


def test_post_at_all_renders_at_all() -> None:
    raw = _post_event(
        {"content": [[{"tag": "at", "user_id": "@_all"}, {"tag": "text", "text": " 通知"}]]}
    )
    ev = FeishuConnector(bot_open_id="ou_bot").parse_inbound(raw)
    assert ev is not None
    assert "@all" in ev.text
