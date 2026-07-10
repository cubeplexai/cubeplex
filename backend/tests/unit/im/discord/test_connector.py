from __future__ import annotations

from unittest.mock import MagicMock

from cubebox.im.discord.connector import DiscordConnector
from cubebox.im.types import DM_SCOPE_KEY


def _make_message(
    *,
    content: str = "hello bot",
    author_id: int = 111,
    author_bot: bool = False,
    channel_id: int = 222,
    message_id: int = 333,
    guild_id: int | None = 444,
    is_dm: bool = False,
    mentions_bot: bool = True,
    mentions_bot_role: bool = False,
    bot_user_id: int = 999,
    thread_id: int | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.id = message_id
    msg.author.id = author_id
    msg.author.bot = author_bot
    msg.channel.id = thread_id or channel_id
    msg.channel.name = "general" if not is_dm else None
    msg.channel.type = MagicMock()
    if is_dm:
        msg.channel.type.value = 1  # DM
        msg.guild = None
    else:
        msg.channel.type.value = 0  # GUILD_TEXT
        msg.guild = MagicMock()
        msg.guild.id = guild_id
    # Thread detection
    if thread_id is not None:
        msg.channel.type.value = 11  # PUBLIC_THREAD
        msg.channel.parent_id = channel_id
        msg.channel.name = "thread-topic"
    # User mentions
    if mentions_bot:
        mention = MagicMock()
        mention.id = bot_user_id
        msg.mentions = [mention]
    else:
        msg.mentions = []
    # Role mentions (bot's managed role)
    if mentions_bot_role:
        role = MagicMock()
        role.id = 888
        role.tags = MagicMock()
        role.tags.bot_id = bot_user_id
        msg.role_mentions = [role]
    else:
        msg.role_mentions = []
    return msg


class TestDiscordConnectorParseInbound:
    def setup_method(self) -> None:
        self.connector = DiscordConnector(bot_user_id=999)

    def test_dm_message(self) -> None:
        msg = _make_message(is_dm=True, mentions_bot=False)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.platform == "discord"
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"
        assert event.text == "hello bot"

    def test_guild_mention(self) -> None:
        msg = _make_message(mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.scope_key == "u:111"
        assert event.scope_kind == "channel"
        assert event.reply_to_id == "333"
        assert event.channel_name == "general"

    def test_dm_has_no_channel_name(self) -> None:
        msg = _make_message(is_dm=True, mentions_bot=False)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.channel_name is None

    def test_guild_no_mention_ignored(self) -> None:
        msg = _make_message(mentions_bot=False)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_bot_message_ignored(self) -> None:
        msg = _make_message(author_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_own_message_ignored(self) -> None:
        msg = _make_message(author_id=999)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_empty_text_ignored(self) -> None:
        msg = _make_message(content="<@999>", mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is None

    def test_thread_message(self) -> None:
        msg = _make_message(
            mentions_bot=True,
            thread_id=555,
            channel_id=222,
        )
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.scope_key == "u:111|t:555"
        assert event.scope_kind == "thread"
        assert event.channel_id == "555"

    def test_role_mention_triggers_bot(self) -> None:
        msg = _make_message(
            content="<@&888> what is 2+2?",
            mentions_bot=False,
            mentions_bot_role=True,
        )
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.text == "what is 2+2?"
        assert event.scope_key == "u:111"

    def test_mention_stripped_from_text(self) -> None:
        msg = _make_message(content="<@999> what is 2+2?", mentions_bot=True)
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.text == "what is 2+2?"

    def test_role_mention_stripped_from_text(self) -> None:
        msg = _make_message(
            content="<@&888> hello there",
            mentions_bot=False,
            mentions_bot_role=True,
        )
        event = self.connector.parse_inbound(msg)
        assert event is not None
        assert event.text == "hello there"
