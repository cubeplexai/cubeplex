"""Slack connector: inbound parse + outbound message send/edit + reactions.

Uses the slack-sdk AsyncWebClient for all outbound API calls.
Inbound parsing works on raw slack-bolt event dicts (no SDK dependency).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from cubeplex.im.outbound import _FloodSignal
from cubeplex.im.slack.format import markdown_to_slack_mrkdwn
from cubeplex.im.types import (
    DM_SCOPE_KEY,
    BindingMode,
    InboundAttachmentRef,
    InboundEvent,
    make_channel_scope,
    make_participant_scope,
    make_thread_participant_scope,
    make_thread_scope,
)

_SECTION_CHAR_LIMIT = 3000


def _parse_slack_files(files: list[dict[str, Any]]) -> list[InboundAttachmentRef]:
    """Build attachment refs from a Slack message's ``files[]`` array.

    ``handle`` is the authenticated download URL; the resolver fetches it with
    the bot token. Slack file inbound is DM-only (see parse_inbound).
    """
    refs: list[InboundAttachmentRef] = []
    for f in files:
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            continue
        mime = f.get("mimetype")
        size = f.get("size")
        kind = "image" if str(mime or "").startswith("image/") else "file"
        refs.append(
            InboundAttachmentRef(
                kind=kind,
                filename=str(f.get("name") or "file"),
                mime=str(mime) if mime else None,
                handle=str(url),
                size_hint=int(size) if isinstance(size, int) else None,
            )
        )
    return refs


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

    def parse_inbound(
        self, raw: dict[str, Any], *, binding_mode: BindingMode = "isolated"
    ) -> InboundEvent | None:
        """Normalize a slack-bolt event dict into an InboundEvent.

        Returns None for messages we should ignore: subtypes, bot messages,
        own messages.
        """
        # Ignore edited/deleted/system messages — but admit ``file_share`` so a
        # user can drop a file (with or without a caption). Other subtypes
        # (bot_message, message_changed, channel_join, …) stay dropped.
        subtype = raw.get("subtype")
        if subtype and subtype != "file_share":
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

        # Slack file inbound is DM-only: a channel mention+file arrives as TWO
        # events (app_mention text + a separate file_share message with no
        # mention), so files are reliably attributable only in a DM. Parse
        # files[] here; only the DM branch below consumes them.
        attachments = _parse_slack_files(raw.get("files") or [])

        if not text and not attachments:
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
                attachments=attachments,
            )

        # Beyond DM, file ingestion is out of scope (channel files arrive as a
        # separate, mention-less file_share event). The app_mention branches
        # below carry text only.
        if not text:
            return None

        # Thread replies (thread_ts present and different from ts)
        if event_type == "app_mention" and thread_ts and thread_ts != ts:
            if binding_mode == "shared":
                scope_key = make_thread_scope(thread_ts)
            else:
                scope_key = make_thread_participant_scope(user, thread_ts)
            return InboundEvent(
                platform="slack",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=channel,
                scope_key=scope_key,
                scope_kind="thread",
                reply_to_id=thread_ts,
                inbound_message_id=ts,
                sender_ref=sender_ref,
                sender_open_id=sender_open_id,
                text=text,
            )

        # Channel @mention (not in a thread)
        if event_type == "app_mention":
            if binding_mode == "shared":
                scope_key = make_channel_scope()
            else:
                scope_key = make_participant_scope(user)
            return InboundEvent(
                platform="slack",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=channel,
                scope_key=scope_key,
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
            logger.opt(exception=True).warning("[Slack] send_message failed")
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
            logger.opt(exception=True).warning("[Slack] edit_message failed")
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
            logger.opt(exception=True).warning("[Slack] send_message_with_blocks failed")
            return None

    async def upload_image(self, local_path: str) -> str | None:
        """Slack has no inline-image key; image artifacts fall back to share-link."""
        del local_path
        return None

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        """Upload + send a native file to the bound channel via files_upload_v2."""
        del mime  # Slack infers type from the filename/bytes.
        if self._client is None or not self._channel_id:
            return False
        try:
            kwargs: dict[str, Any] = {
                "channel": self._channel_id,
                "file": local_path,
                "filename": filename,
            }
            if self._thread_ts:
                kwargs["thread_ts"] = self._thread_ts
            await self._client.files_upload_v2(**kwargs)
            return True
        except Exception:
            logger.opt(exception=True).warning("[Slack] send_file failed")
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
            logger.opt(exception=True).warning("[Slack] add_reaction failed")
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
            logger.opt(exception=True).warning("[Slack] remove_reaction failed")
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
            logger.opt(exception=True).warning("[Slack] resolve_email failed for {}", open_id)
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
            logger.opt(exception=True).warning("[Slack] send_to_chat failed")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        """Check if an exception indicates a Slack rate-limit error."""
        msg = str(exc).lower()
        return "ratelimited" in msg or "rate_limited" in msg
