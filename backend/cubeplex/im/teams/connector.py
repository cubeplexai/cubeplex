"""Teams connector: inbound parse + outbound message send/edit + identity."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.im.outbound import _FloodSignal
from cubeplex.im.teams.app_manager import get_conversation_route
from cubeplex.im.teams.format import normalize_for_teams, strip_mention_tags
from cubeplex.im.types import (
    DM_SCOPE_KEY,
    InboundEvent,
    RenderState,
    make_participant_scope,
    make_thread_participant_scope,
)

TEAMS_MSG_LIMIT = 25000
_DEFAULT_SERVICE_URL = "https://smba.trafficmanager.net/teams"


class TeamsRateLimitError(_FloodSignal):
    """Raised when Teams API returns HTTP 429."""


class TeamsConnector:
    """Connector for one Teams bot account.

    Construction:
    - Inbound parsing only needs ``bot_id``.
    - Outbound calls need an ``app`` (microsoft-teams SDK App instance)
      plus ``channel_id`` (conversation id) and optionally ``reply_to_id``.
    - ``service_url`` / ``bf_channel_id`` must come from the inbound activity
      (Web Chat uses a different serviceUrl than Teams; App.send hardcodes
      msteams + default smba URL and 401s on Web Chat).
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
        service_url: str | None = None,
        bf_channel_id: str | None = None,
        bot_account_id: str | None = None,
    ) -> None:
        self._bot_id = bot_id
        self._app = app
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id
        self._graph_client = graph_client
        self._service_url = (service_url or "").rstrip("/") or None
        self._bf_channel_id = bf_channel_id or None
        # Channel account id from activity.recipient (not always the App ID).
        self._bot_account_id = (bot_account_id or "").strip() or None
        # Web Chat / Direct Line reject PUT updateActivity (405). Cache the
        # first failure so we stop trying mid-stream.
        self._edit_supported: bool | None = None

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
        # Group chats often include a display name; channel activities may not.
        raw_conv_name = conversation.get("name")
        channel_name = (
            str(raw_conv_name).strip()
            if isinstance(raw_conv_name, str) and raw_conv_name.strip()
            else None
        )
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
                channel_name=channel_name,
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
            channel_name=channel_name,
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

    @property
    def supports_message_edit(self) -> bool:
        """Whether this channel supports updating an existing activity.

        Teams does (updateActivity streaming). Web Chat / Direct Line return
        405 Method Not Allowed on PUT …/activities/{id}.
        """
        if self._edit_supported is False:
            return False
        if self._edit_supported is True:
            return True
        ch = (self._bf_channel_id or "msteams").lower()
        # Known non-updatable Bot Framework channels.
        if ch in ("webchat", "directline", "emulator"):
            return False
        return True

    def _resolve_route(self, conversation_id: str) -> tuple[str, str, str]:
        """Pick service_url, channel id, and bot channel account for outbound."""
        if self._service_url:
            return (
                self._service_url,
                self._bf_channel_id or "msteams",
                self._bot_account_id or self._bot_id,
            )
        cached = get_conversation_route(conversation_id)
        if cached is not None:
            url, ch, bot_acc = cached
            return url, ch, bot_acc or self._bot_account_id or self._bot_id
        default_url = _DEFAULT_SERVICE_URL
        if self._app is not None:
            api = getattr(self._app, "api", None)
            api_url = getattr(api, "service_url", None) if api is not None else None
            if isinstance(api_url, str) and api_url.strip():
                default_url = api_url.rstrip("/")
        return (
            default_url,
            self._bf_channel_id or "msteams",
            self._bot_account_id or self._bot_id,
        )

    async def _send_activity(
        self,
        conversation_id: str,
        activity: Any,
    ) -> Any:
        """Send via activity_sender with the conversation's real serviceUrl.

        ``App.send`` hardcodes ``channel_id="msteams"``, the default Teams
        service URL, and ``from.id`` = App ID. Web Chat requires the inbound
        ``activity.recipient.id`` as from.id or the connector returns 403.
        """
        from microsoft_teams.api import (
            Account,
            ConversationAccount,
            ConversationReference,
            MessageActivityInput,
        )
        from microsoft_teams.cards import AdaptiveCard

        if self._app is None:
            raise RuntimeError("Teams app not bound")
        if not getattr(self._app, "_initialized", True):
            raise RuntimeError("Teams app not initialized")

        service_url, bf_channel, bot_account_id = self._resolve_route(conversation_id)
        if not bot_account_id:
            bot_account_id = getattr(self._app, "id", None) or self._bot_id
        if not bot_account_id:
            raise RuntimeError("Teams bot account id missing")

        if isinstance(activity, str):
            activity = MessageActivityInput(text=activity)
        elif isinstance(activity, AdaptiveCard):
            activity = MessageActivityInput().add_card(activity)

        ref = ConversationReference(
            channel_id=bf_channel,
            service_url=service_url,
            bot=Account(id=str(bot_account_id)),
            conversation=ConversationAccount(id=conversation_id),
        )
        sender = getattr(self._app, "activity_sender", None)
        if sender is None:
            # Fallback for older SDK shapes; still better than wrong URL.
            return await self._app.send(conversation_id, activity)
        return await sender.send(activity, ref)

    async def send_message(self, text: str) -> str | None:
        """Send a Markdown text message. Returns the activity ID."""
        if self._app is None or not self._channel_id:
            return None
        try:
            text = normalize_for_teams(text[:TEAMS_MSG_LIMIT])
            result = await self._send_activity(self._channel_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.opt(exception=True).warning("[Teams] send_message failed")
            return None

    async def edit_message(self, activity_id: str, text: str) -> bool:
        """Update an existing message. Raises TeamsRateLimitError on 429.

        Returns False when the channel does not support updates (Web Chat
        405) so the renderer can fall back to send-new-message.
        """
        if self._app is None or not self._channel_id:
            return False
        if not self.supports_message_edit:
            return False
        try:
            from microsoft_teams.api import MessageActivityInput

            text = normalize_for_teams(text[:TEAMS_MSG_LIMIT])
            msg = MessageActivityInput()
            msg.id = activity_id
            msg.text = text
            await self._send_activity(self._channel_id, msg)
            self._edit_supported = True
            return True
        except Exception as exc:
            if _is_rate_limit(exc):
                raise TeamsRateLimitError(f"edit rate limited: {exc}") from exc
            # Web Chat / some connectors: PUT activities/{id} → 405.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 405 or "405" in str(exc):
                self._edit_supported = False
                logger.info(
                    "[Teams] edit not supported on channel={} — using send-only mode",
                    self._bf_channel_id,
                )
                return False
            logger.opt(exception=True).warning("[Teams] edit_message failed")
            return False

    async def send_typing(self) -> None:
        if self._app is None or not self._channel_id:
            return
        try:
            from microsoft_teams.api.activities.typing import (
                TypingActivityInput,
            )

            await self._send_activity(self._channel_id, TypingActivityInput())
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
            result = await self._send_activity(self._channel_id, msg)
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

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        """Teams native file send needs Graph/SharePoint drive upload (out of
        scope); the artifact dispatcher falls back to a share-link."""
        del local_path, filename, mime
        return False

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        if self._app is None:
            return None
        del reply_to_id  # Bot Framework create-activity path; threading via conv id
        try:
            result = await self._send_activity(chat_id, text)
            return str(result.id) if result and hasattr(result, "id") else None
        except Exception:
            logger.opt(exception=True).warning(
                "[Teams] send_to_chat failed chat_id={} route={}",
                chat_id,
                self._resolve_route(chat_id),
            )
            return None


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or ("rate" in msg and "limit" in msg)
