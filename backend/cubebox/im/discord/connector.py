"""Discord connector: inbound parse + outbound message send/edit + reactions.

All discord.py API calls are async-native (no to_thread needed unlike
Feishu's sync SDK).
"""

from __future__ import annotations

import re
from typing import Any

import discord
from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.types import (
    DM_SCOPE_KEY,
    BindingMode,
    InboundEvent,
    make_channel_scope,
    make_participant_scope,
    make_thread_participant_scope,
    make_thread_scope,
)

_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")

_REACTION_PROCESSING = "⏳"
_REACTION_FAILURE = "❌"


class DiscordRateLimitError(_FloodSignal):
    """Raised when Discord API returns 429."""


class DiscordConnector:
    """Connector for one Discord bot account.

    Construction:
    - Inbound parsing only needs ``bot_user_id``.
    - Outbound calls need a bound ``bot`` (discord.py Bot instance)
      plus ``channel_id``, set at tailer construction time.
    """

    def __init__(
        self,
        *,
        bot_user_id: int | None = None,
        bot: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._bot = bot
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    def _clean_mentions(self, text: str, message: Any) -> str:
        """Strip the bot's own @mention and role mention; replace others with display names."""

        def _replace_user(match: re.Match[str]) -> str:
            uid = int(match.group(1))
            if self._bot_user_id is not None and uid == self._bot_user_id:
                return ""
            for m in getattr(message, "mentions", []):
                if int(m.id) == uid:
                    return f"@{m.display_name}"
            return match.group(0)

        def _replace_role(match: re.Match[str]) -> str:
            rid = int(match.group(1))
            for role in getattr(message, "role_mentions", []):
                tags = getattr(role, "tags", None)
                if tags and getattr(tags, "bot_id", None) == self._bot_user_id:
                    if int(role.id) == rid:
                        return ""
            return match.group(0)

        text = _USER_MENTION_RE.sub(_replace_user, text)
        text = _ROLE_MENTION_RE.sub(_replace_role, text)
        return text.strip()

    def parse_inbound(
        self, message: Any, *, binding_mode: BindingMode = "isolated"
    ) -> InboundEvent | None:
        """Normalize one discord.py Message into InboundEvent.

        Returns None for messages we ignore: bot authors, own messages,
        non-text, guild messages that don't mention the bot, empty text.
        """
        if getattr(message.author, "bot", False):
            return None
        author_id = message.author.id
        if self._bot_user_id is not None and author_id == self._bot_user_id:
            return None

        channel = message.channel
        channel_type = getattr(channel, "type", None)
        channel_type_value = getattr(channel_type, "value", -1)

        is_dm = channel_type_value == 1
        is_thread = channel_type_value in (11, 12)  # PUBLIC_THREAD, PRIVATE_THREAD

        text = str(message.content or "")
        text = self._clean_mentions(text, message)
        if not text:
            return None

        message_id = str(message.id)
        sender_ref = str(author_id)

        if is_dm:
            return InboundEvent(
                platform="discord",
                account_external_id="",
                platform_event_id=message_id,
                channel_id=str(channel.id),
                scope_key=DM_SCOPE_KEY,
                scope_kind="dm",
                reply_to_id=None,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=sender_ref,
                text=text,
            )

        if not self._mentions_bot(message):
            return None

        if is_thread:
            thread_id = str(channel.id)
            if binding_mode == "shared":
                scope_key = make_thread_scope(thread_id)
            else:
                scope_key = make_thread_participant_scope(sender_ref, thread_id)
            return InboundEvent(
                platform="discord",
                account_external_id="",
                platform_event_id=message_id,
                channel_id=thread_id,
                scope_key=scope_key,
                scope_kind="thread",
                reply_to_id=message_id,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=sender_ref,
                text=text,
            )

        if binding_mode == "shared":
            scope_key = make_channel_scope()
        else:
            scope_key = make_participant_scope(sender_ref)
        return InboundEvent(
            platform="discord",
            account_external_id="",
            platform_event_id=message_id,
            channel_id=str(channel.id),
            scope_key=scope_key,
            scope_kind="channel",
            reply_to_id=message_id,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=sender_ref,
            text=text,
        )

    def _mentions_bot(self, message: Any) -> bool:
        if self._bot_user_id is None:
            return False
        for mention in getattr(message, "mentions", []):
            if getattr(mention, "id", None) == self._bot_user_id:
                return True
        for role in getattr(message, "role_mentions", []):
            tags = getattr(role, "tags", None)
            if tags and getattr(tags, "bot_id", None) == self._bot_user_id:
                return True
        return False

    async def send_message(self, text: str) -> str | None:
        """Send a message to the bound channel. Returns message_id."""
        if self._bot is None or not self._channel_id:
            return None
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.send(text)
            return str(msg.id)
        except Exception:
            logger.warning("[Discord] send_message failed", exc_info=True)
            return None

    async def edit_message(self, message_id: str, text: str) -> bool:
        """Edit an existing message. Returns True on success."""
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=text)
            return True
        except discord.HTTPException as exc:
            if exc.status == 429:
                raise DiscordRateLimitError(f"edit rate limited: {exc}") from exc
            logger.warning("[Discord] edit_message failed", exc_info=True)
            return False
        except Exception:
            logger.warning("[Discord] edit_message failed", exc_info=True)
            return False

    async def add_reaction(self, message_id: str, emoji: str) -> bool:
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            return True
        except Exception:
            logger.warning("[Discord] add_reaction failed", exc_info=True)
            return False

    async def remove_reaction(self, message_id: str, emoji: str) -> bool:
        if self._bot is None or not self._channel_id:
            return False
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.remove_reaction(emoji, self._bot.user)
            return True
        except Exception:
            logger.warning("[Discord] remove_reaction failed", exc_info=True)
            return False

    async def on_processing_start(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if target:
            await self.add_reaction(target, _REACTION_PROCESSING)

    async def on_processing_complete(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if target:
            await self.remove_reaction(target, _REACTION_PROCESSING)

    async def on_processing_failed(self, state: Any) -> None:
        target = getattr(state, "inbound_message_id", None)
        if not target:
            return
        await self.remove_reaction(target, _REACTION_PROCESSING)
        await self.add_reaction(target, _REACTION_FAILURE)

    async def send_message_with_view(self, text: str, view: Any) -> str | None:
        """Send a message with an interactive View (buttons). Returns message_id."""
        if self._bot is None or not self._channel_id:
            return None
        try:
            channel = self._bot.get_channel(int(self._channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(self._channel_id))
            msg = await channel.send(text, view=view)
            return str(msg.id)
        except Exception:
            logger.warning("[Discord] send_message_with_view failed", exc_info=True)
            return None

    async def _send_emergency_text(self, text: str) -> str | None:
        return await self.send_message(text)

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        if self._bot is None:
            return None
        try:
            channel = self._bot.get_channel(int(chat_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(chat_id))
            if reply_to_id:
                try:
                    ref_msg = await channel.fetch_message(int(reply_to_id))
                    msg = await channel.send(text, reference=ref_msg)
                except Exception:
                    msg = await channel.send(text)
            else:
                msg = await channel.send(text)
            return str(msg.id)
        except Exception:
            logger.warning("[Discord] send_to_chat failed", exc_info=True)
            return None
