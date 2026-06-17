"""Slack connector: inbound parse + outbound message send/edit + reactions.

Uses the slack-sdk AsyncWebClient for all outbound API calls.
Inbound parsing works on raw slack-bolt event dicts (no SDK dependency).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.slack.format import markdown_to_slack_mrkdwn
from cubebox.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    make_participant_scope,
    make_thread_participant_scope,
)

_SECTION_CHAR_LIMIT = 3000


class SlackRateLimitError(_FloodSignal):
    """Raised when Slack API returns a rate-limit error."""


class SlackConnector:
    """Connector for one Slack bot account.

    Construction:
    - Inbound parsing only needs ``bot_user_id``.
    - Outbound calls need a ``client`` (slack-sdk AsyncWebClient)
      plus ``channel_id`` and optionally ``thread_ts``.
    """

    def __init__(
        self,
        *,
        bot_user_id: str = "",
        client: Any = None,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._mention_re = re.compile(rf"<@{re.escape(bot_user_id)}>") if bot_user_id else None

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        """Normalize a slack-bolt event dict into an InboundEvent.

        Returns None for messages we should ignore: subtypes, bot messages,
        own messages.
        """
        # Ignore edited/deleted/system messages
        if raw.get("subtype"):
            return None

        user: str = raw.get("user", "")
        # Ignore bot's own messages
        if user and user == self._bot_user_id:
            return None
        # Ignore messages from other bots
        if raw.get("bot_id"):
            return None

        text: str = raw.get("text", "")
        channel: str = raw.get("channel", "")
        ts: str = raw.get("ts", "")
        thread_ts: str = raw.get("thread_ts", "")
        channel_type: str = raw.get("channel_type", "")
        event_type: str = raw.get("type", "")

        if self._mention_re:
            text = self._mention_re.sub("", text).strip()

        if not text:
            return None

        # Platform event ID: prefer client_msg_id, fall back to channel:ts
        platform_event_id = raw.get("client_msg_id") or f"{channel}:{ts}"

        sender_ref = user
        sender_open_id = user

        # DM messages
        if channel_type == "im":
            return InboundEvent(
                platform="slack",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=channel,
                scope_key=DM_SCOPE_KEY,
                scope_kind="dm",
                reply_to_id=None,
                inbound_message_id=ts,
                sender_ref=sender_ref,
                sender_open_id=sender_open_id,
                text=text,
            )

        # Thread replies (thread_ts present and different from ts)
        if event_type == "app_mention" and thread_ts and thread_ts != ts:
            return InboundEvent(
                platform="slack",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=channel,
                scope_key=make_thread_participant_scope(user, thread_ts),
                scope_kind="thread",
                reply_to_id=thread_ts,
                inbound_message_id=ts,
                sender_ref=sender_ref,
                sender_open_id=sender_open_id,
                text=text,
            )

        # Channel @mention (not in a thread)
        if event_type == "app_mention":
            return InboundEvent(
                platform="slack",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=channel,
                scope_key=make_participant_scope(user),
                scope_kind="channel",
                reply_to_id=ts,
                inbound_message_id=ts,
                sender_ref=sender_ref,
                sender_open_id=sender_open_id,
                text=text,
            )

        return None

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def _make_section_blocks(self, text: str) -> list[dict[str, Any]]:
        """Build Block Kit section blocks, truncating text to the limit."""
        mrkdwn_text = markdown_to_slack_mrkdwn(text)[:_SECTION_CHAR_LIMIT]
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": mrkdwn_text},
            }
        ]

    async def send_message(self, text: str) -> str | None:
        """Post a Block Kit message. Returns the message ``ts``."""
        if self._client is None or not self._channel_id:
            return None
        try:
            blocks = self._make_section_blocks(text)
            kwargs: dict[str, Any] = {
                "channel": self._channel_id,
                "blocks": blocks,
                "text": text[:_SECTION_CHAR_LIMIT],
            }
            if self._thread_ts:
                kwargs["thread_ts"] = self._thread_ts
            resp = await self._client.chat_postMessage(**kwargs)
            return str(resp["ts"]) if resp and resp.get("ts") else None
        except Exception:
            logger.warning("[Slack] send_message failed", exc_info=True)
            return None

    async def edit_message(self, message_ts: str, text: str) -> bool:
        """Update an existing message. Raises SlackRateLimitError on 429."""
        if self._client is None or not self._channel_id:
            return False
        try:
            blocks = self._make_section_blocks(text)
            await self._client.chat_update(
                channel=self._channel_id,
                ts=message_ts,
                blocks=blocks,
                text=text[:_SECTION_CHAR_LIMIT],
            )
            return True
        except Exception as exc:
            if self._is_rate_limit(exc):
                raise SlackRateLimitError(f"edit rate limited: {exc}") from exc
            logger.warning("[Slack] edit_message failed", exc_info=True)
            return False

    async def send_message_with_blocks(self, blocks: list[dict[str, Any]], text: str) -> str | None:
        """Post with custom blocks (e.g. buttons). Returns message ``ts``."""
        if self._client is None or not self._channel_id:
            return None
        try:
            kwargs: dict[str, Any] = {
                "channel": self._channel_id,
                "blocks": blocks,
                "text": text[:_SECTION_CHAR_LIMIT],
            }
            if self._thread_ts:
                kwargs["thread_ts"] = self._thread_ts
            resp = await self._client.chat_postMessage(**kwargs)
            return str(resp["ts"]) if resp and resp.get("ts") else None
        except Exception:
            logger.warning("[Slack] send_message_with_blocks failed", exc_info=True)
            return None

    async def update_message_with_blocks(
        self, message_ts: str, blocks: list[dict[str, Any]], text: str
    ) -> bool:
        """Update with custom blocks."""
        if self._client is None or not self._channel_id:
            return False
        try:
            await self._client.chat_update(
                channel=self._channel_id,
                ts=message_ts,
                blocks=blocks,
                text=text[:_SECTION_CHAR_LIMIT],
            )
            return True
        except Exception as exc:
            if self._is_rate_limit(exc):
                raise SlackRateLimitError(f"update rate limited: {exc}") from exc
            logger.warning("[Slack] update_message_with_blocks failed", exc_info=True)
            return False

    async def add_reaction(self, message_ts: str, emoji: str) -> bool:
        """Add an emoji reaction to a message."""
        if self._client is None or not self._channel_id:
            return False
        try:
            await self._client.reactions_add(
                channel=self._channel_id,
                timestamp=message_ts,
                name=emoji,
            )
            return True
        except Exception:
            logger.warning("[Slack] add_reaction failed", exc_info=True)
            return False

    async def remove_reaction(self, message_ts: str, emoji: str) -> bool:
        """Remove an emoji reaction from a message."""
        if self._client is None or not self._channel_id:
            return False
        try:
            await self._client.reactions_remove(
                channel=self._channel_id,
                timestamp=message_ts,
                name=emoji,
            )
            return True
        except Exception:
            logger.warning("[Slack] remove_reaction failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # IdentityResolver protocol
    # ------------------------------------------------------------------

    async def resolve_email(self, open_id: str) -> str | None:
        """Resolve a Slack user ID to their email address."""
        if self._client is None:
            return None
        try:
            resp = await self._client.users_info(user=open_id)
            if resp and resp.get("ok"):
                user_data: dict[str, Any] = resp.get("user", {})
                profile: dict[str, Any] = user_data.get("profile", {})
                return profile.get("email") or None
            return None
        except Exception:
            logger.warning("[Slack] resolve_email failed for %s", open_id, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # RejectionNotifier protocol
    # ------------------------------------------------------------------

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        """Send plain text to a channel (for rejection notices etc.)."""
        if self._client is None:
            return None
        try:
            kwargs: dict[str, Any] = {
                "channel": chat_id,
                "text": text,
            }
            if reply_to_id:
                kwargs["thread_ts"] = reply_to_id
            resp = await self._client.chat_postMessage(**kwargs)
            return str(resp["ts"]) if resp and resp.get("ts") else None
        except Exception:
            logger.warning("[Slack] send_to_chat failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        """Check if an exception indicates a Slack rate-limit error."""
        msg = str(exc).lower()
        return "ratelimited" in msg or "rate_limited" in msg
