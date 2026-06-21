"""Teams connector: inbound parse + outbound message send/edit + identity."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.teams.format import normalize_for_teams, strip_mention_tags
from cubebox.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    RenderState,
    make_participant_scope,
    make_thread_participant_scope,
)

TEAMS_MSG_LIMIT = 25000


class TeamsRateLimitError(_FloodSignal):
    """Raised when Teams API returns HTTP 429."""


class TeamsConnector:
    """Connector for one Teams bot account.

    Construction:
    - Inbound parsing only needs ``bot_id``.
    - Outbound calls need an ``app`` (microsoft-teams SDK App instance)
      plus ``channel_id`` and optionally ``reply_to_id``.
    - Identity resolution needs a ``graph_client`` (TeamsGraphClient).
    """

    def __init__(
        self,
        *,
        bot_id: str = "",
        app: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
        graph_client: Any = None,
    ) -> None:
        self._bot_id = bot_id
        self._app = app
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id
        self._graph_client = graph_client

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, activity: dict[str, Any]) -> InboundEvent | None:
        """Normalize a Bot Framework activity dict into an InboundEvent."""
        if activity.get("type") != "message":
            return None

        from_obj: dict[str, Any] = activity.get("from", {})
        sender_id: str = str(from_obj.get("id") or "")
        aad_object_id: str = str(from_obj.get("aadObjectId") or "")

        if sender_id == self._bot_id:
            return None

        conversation: dict[str, Any] = activity.get("conversation", {})
        conv_type: str = str(conversation.get("conversationType") or "personal")
        conv_id: str = str(conversation.get("id") or "")
        message_id: str = str(activity.get("id") or "")
        reply_to_id: str | None = activity.get("replyToId")

        text: str = str(activity.get("text") or "")
        text = strip_mention_tags(text)

        is_dm = conv_type == "personal"
        is_mentioned = self._is_bot_mentioned(activity) if not is_dm else True

        if not is_dm and not is_mentioned:
            return None

        if not text.strip():
            return None

        sender_ref = aad_object_id or sender_id
        platform_event_id = message_id or conv_id

        if is_dm:
            return InboundEvent(
                platform="teams",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=conv_id,
                scope_key=DM_SCOPE_KEY,
                scope_kind="dm",
                reply_to_id=None,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=aad_object_id or None,
                text=text.strip(),
            )

        if conv_type == "channel" and reply_to_id:
            return InboundEvent(
                platform="teams",
                account_external_id="",
                platform_event_id=platform_event_id,
                channel_id=conv_id,
                scope_key=make_thread_participant_scope(sender_ref, reply_to_id),
                scope_kind="thread",
                reply_to_id=reply_to_id,
                inbound_message_id=message_id,
                sender_ref=sender_ref,
                sender_open_id=aad_object_id or None,
                text=text.strip(),
            )

        scope_kind = "group" if conv_type == "groupChat" else "channel"
        return InboundEvent(
            platform="teams",
            account_external_id="",
            platform_event_id=platform_event_id,
            channel_id=conv_id,
            scope_key=make_participant_scope(sender_ref),
            scope_kind=scope_kind,
            reply_to_id=message_id,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=aad_object_id or None,
            text=text.strip(),
        )

    def _is_bot_mentioned(self, activity: dict[str, Any]) -> bool:
        for entity in activity.get("entities", []):
            if entity.get("type") != "mention":
                continue
            mentioned: dict[str, Any] = entity.get("mentioned", {})
            if str(mentioned.get("id") or "") == self._bot_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> str | None:
        """Send a Markdown text message. Returns the activity ID."""
        if self._app is None or not self._channel_id:
            return None
        try:
            text = normalize_for_teams(text[:TEAMS_MSG_LIMIT])
            result = await self._app.send(self._channel_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.opt(exception=True).warning("[Teams] send_message failed")
            return None

    async def edit_message(self, activity_id: str, text: str) -> bool:
        """Update an existing message. Raises TeamsRateLimitError on 429."""
        if self._app is None or not self._channel_id:
            return False
        try:
            from microsoft_teams.api import MessageActivityInput

            text = normalize_for_teams(text[:TEAMS_MSG_LIMIT])
            msg = MessageActivityInput()
            msg.id = activity_id
            msg.text = text
            await self._app.send(self._channel_id, msg)
            return True
        except Exception as exc:
            if _is_rate_limit(exc):
                raise TeamsRateLimitError(f"edit rate limited: {exc}") from exc
            logger.opt(exception=True).warning("[Teams] edit_message failed")
            return False

    async def send_typing(self) -> None:
        if self._app is None or not self._channel_id:
            return
        try:
            from microsoft_teams.api.activities.typing import (
                TypingActivityInput,
            )

            await self._app.send(self._channel_id, TypingActivityInput())
        except Exception:
            logger.opt(exception=True).debug("[Teams] typing indicator failed")

    async def send_card(self, card: dict[str, Any]) -> str | None:
        if self._app is None or not self._channel_id:
            return None
        try:
            from microsoft_teams.api import (
                Attachment,
                MessageActivityInput,
            )

            attachment = Attachment(
                content_type="application/vnd.microsoft.card.adaptive",
                content=card,
            )
            msg = MessageActivityInput(attachments=[attachment])
            result = await self._app.send(self._channel_id, msg)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.opt(exception=True).warning("[Teams] send_card failed")
            return None

    # ------------------------------------------------------------------
    # Processing lifecycle hooks
    # ------------------------------------------------------------------

    async def on_processing_start(self, state: RenderState) -> None:
        await self.send_typing()

    async def on_processing_complete(self, state: RenderState) -> None:
        pass

    async def on_processing_failed(self, state: RenderState) -> None:
        pass

    # ------------------------------------------------------------------
    # IdentityResolver protocol
    # ------------------------------------------------------------------

    async def resolve_email(self, open_id: str) -> str | None:
        if self._graph_client is None:
            return None
        result: str | None = await self._graph_client.get_user_email(open_id)
        return result

    # ------------------------------------------------------------------
    # RejectionNotifier protocol
    # ------------------------------------------------------------------

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        if self._app is None:
            return None
        try:
            result = await self._app.send(chat_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.opt(exception=True).warning("[Teams] send_to_chat failed")
            return None


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or ("rate" in msg and "limit" in msg)
