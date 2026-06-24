"""Unit tests for account-level IM bot settings (pure helpers)."""

from __future__ import annotations

from cubebox.im.bot_settings import (
    IMBotSettings,
    bot_display_name,
    build_im_attributes,
    im_topic_title,
    load_bot_settings,
    store_bot_settings,
    wants_topic,
)


class TestLoadStore:
    def test_defaults_on_empty_config(self) -> None:
        s = load_bot_settings(None)
        assert s.routing_mode == "isolated"
        assert s.topic_mode == "topic"
        assert s.sandbox_mode is None

    def test_defaults_on_missing_key(self) -> None:
        assert load_bot_settings({"other": 1}).routing_mode == "isolated"

    def test_roundtrip(self) -> None:
        s = IMBotSettings(routing_mode="shared", topic_mode="flat", sandbox_mode="dedicated")
        cfg = store_bot_settings({"keep": "me"}, s)
        assert cfg["keep"] == "me"  # preserves unrelated config
        assert load_bot_settings(cfg) == s

    def test_invalid_blob_falls_back_to_defaults(self) -> None:
        assert load_bot_settings({"bot_settings": "nonsense"}) == IMBotSettings()
        assert load_bot_settings({"bot_settings": {"routing_mode": "bogus"}}) == IMBotSettings()


class TestWantsTopic:
    def test_topic_mode_on(self) -> None:
        assert wants_topic(IMBotSettings(routing_mode="isolated", topic_mode="topic")) is True

    def test_flat_isolated_off(self) -> None:
        assert wants_topic(IMBotSettings(routing_mode="isolated", topic_mode="flat")) is False

    def test_shared_topic_on(self) -> None:
        assert wants_topic(IMBotSettings(routing_mode="shared", topic_mode="topic")) is True

    def test_shared_flat_off(self) -> None:
        # topic_mode is now orthogonal to routing_mode — shared+flat is valid
        # (one group conversation, ungrouped in the sidebar).
        assert wants_topic(IMBotSettings(routing_mode="shared", topic_mode="flat")) is False


class TestTitleAndAttributes:
    def test_dm_title_is_bot_name(self) -> None:
        assert im_topic_title(scope_kind="dm", bot_name="MyBot", channel_name="ignored") == "MyBot"

    def test_group_title_is_channel_name(self) -> None:
        assert im_topic_title(scope_kind="channel", bot_name="MyBot", channel_name="Team") == "Team"

    def test_group_title_falls_back_when_no_channel_name(self) -> None:
        assert im_topic_title(scope_kind="channel", bot_name="MyBot", channel_name=None) == "群聊"

    def test_bot_display_name_default(self) -> None:
        assert bot_display_name(None) == "cubebox"
        assert bot_display_name({"bot_app_name": "Helper"}) == "Helper"

    def test_attributes_shape(self) -> None:
        attrs = build_im_attributes(
            platform="feishu",
            account_id="ima_x",
            scope_kind="dm",
            bot_name="MyBot",
            bot_avatar_url=None,
            channel_id="oc_1",
            channel_name=None,
        )
        assert attrs["im"]["platform"] == "feishu"
        assert attrs["im"]["account_id"] == "ima_x"
        assert attrs["im"]["scope_kind"] == "dm"
        assert attrs["im"]["bot_name"] == "MyBot"
