"""Feishu connector: inbound parse + outbound send/edit/react (lark_oapi)."""

import json
import re
from typing import Any

from cubebox.im.types import DM_SCOPE_KEY, InboundEvent, make_participant_scope

# Matches Feishu inline mention markup: <at user_id="ou_xxx">name</at>
_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>", re.DOTALL)


class FeishuConnector:
    """Connector for one Feishu account.

    Construction comes in two stages:
    - Inbound parsing only needs ``bot_open_id`` (for mention gating).
    - Outbound calls need a bound ``lark_oapi.Client`` plus ``channel_id``
      and (optional) ``reply_to_id``, set via ``bind_outbound()`` before use.
    """

    def __init__(
        self,
        *,
        bot_open_id: str | None = None,
        client: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        self._bot_open_id = bot_open_id
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        """Normalize one Feishu im.message.receive_v1 payload into InboundEvent.

        Returns ``None`` for events we ignore: bot's own messages, non-text
        message types in v1, empty-after-strip text, group messages that do
        not mention the bot (defense-in-depth — Feishu subscription is the
        primary gate).
        """
        header = raw.get("header") or {}
        event = raw.get("event") or {}
        sender = event.get("sender") or {}
        message = event.get("message") or {}

        if header.get("event_type") != "im.message.receive_v1":
            return None
        sender_id = sender.get("sender_id") or {}
        sender_open_id = sender_id.get("open_id")
        if sender.get("sender_type") == "app":
            return None
        if self._bot_open_id is not None and sender_open_id == self._bot_open_id:
            return None
        if message.get("message_type") != "text":
            return None

        try:
            content_obj = json.loads(message.get("content", "{}"))
        except json.JSONDecodeError:
            return None
        text = _AT_TAG_RE.sub("", content_obj.get("text", "")).strip()
        if not text:
            return None

        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")

        sender_ref = sender_id.get("union_id") or sender_open_id or ""

        if chat_type == "p2p":
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"
            reply_target: str | None = None
        else:
            if not self._group_message_mentions_bot(message):
                return None
            if not sender_ref:
                return None
            scope_key = make_participant_scope(sender_ref)
            scope_kind = "participant"
            reply_target = message_id

        return InboundEvent(
            platform="feishu",
            account_external_id="",  # ingress fills this from account lookup
            platform_event_id=header.get("event_id", ""),
            channel_id=chat_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=reply_target,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=sender_open_id,
            text=text,
        )

    def _group_message_mentions_bot(self, message: dict[str, Any]) -> bool:
        """Defense-in-depth gate for group messages.

        If the Feishu subscription is misconfigured to ``group_msg`` instead
        of ``group_at_msg``, we still drop everything that does not @ the
        bot. When ``bot_open_id`` has not yet been hydrated (PoC/dev path),
        pass through — startup glue is responsible for hydrating before
        events start arriving.
        """
        if self._bot_open_id is None:
            return True
        for mention in message.get("mentions") or []:
            mid = (mention.get("id") or {}).get("open_id")
            if mid and mid == self._bot_open_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Outbound binding
    # ------------------------------------------------------------------

    def bind_outbound(
        self,
        *,
        client: Any,
        channel_id: str,
        reply_to_id: str | None,
    ) -> None:
        """Bind the SDK client + target channel/thread for outbound calls.

        Called by the worker's ``on_run_started`` callback before the tailer
        starts emitting ops. The same instance is reused for the run's
        lifetime.
        """
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id
