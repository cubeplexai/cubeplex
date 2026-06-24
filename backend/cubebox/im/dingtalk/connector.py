"""DingTalk connector: inbound parse + outbound card/message API + identity."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from loguru import logger

from cubebox.im.outbound import _FloodSignal
from cubebox.im.types import (
    DM_SCOPE_KEY,
    BindingMode,
    InboundEvent,
    make_channel_scope,
    make_participant_scope,
)


class DingtalkRateLimitError(_FloodSignal):
    """Raised when DingTalk API returns a rate-limit error."""


class DingtalkConnector:
    """Connector for one DingTalk enterprise bot account.

    Construction:
    - Inbound parsing only needs ``bot_user_id``.
    - Outbound calls need ``access_token`` + ``conversation_id``.
    """

    def __init__(
        self,
        *,
        bot_user_id: str = "",
        access_token: str = "",
        conversation_id: str = "",
        sender_staff_id: str = "",
        is_dm: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._access_token = access_token
        self._conversation_id = conversation_id
        self._sender_staff_id = sender_staff_id
        self._is_dm = is_dm
        self._http_ext = http_client
        self._http_own: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._http_ext is not None:
            return self._http_ext
        if self._http_own is None:
            self._http_own = httpx.AsyncClient(timeout=10)
        return self._http_own

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(
        self, raw: dict[str, Any], *, binding_mode: BindingMode = "isolated"
    ) -> InboundEvent | None:
        """Normalize a DingTalk Stream callback dict into an InboundEvent.

        Returns None for non-text messages.
        """
        msgtype = raw.get("msgtype", "")
        if msgtype != "text":
            return None

        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip()
        msg_id: str = raw.get("msgId", "")
        conversation_id: str = raw.get("conversationId", "")
        conversation_type: str = raw.get("conversationType", "")
        sender_staff_id: str = raw.get("senderStaffId", "")

        if not msg_id or not conversation_id or not sender_staff_id:
            return None

        if conversation_type == "1":
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"
        else:
            is_at_bot = raw.get("isInAtList", False)
            if not is_at_bot:
                return None
            if binding_mode == "shared":
                scope_key = make_channel_scope()
                scope_kind = "channel"
            else:
                scope_key = make_participant_scope(sender_staff_id)
                scope_kind = "group"
            text = re.sub(r"^\s+", "", text)

        return InboundEvent(
            platform="dingtalk",
            account_external_id="",
            platform_event_id=msg_id,
            channel_id=conversation_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=msg_id,
            inbound_message_id=msg_id,
            sender_ref=sender_staff_id,
            sender_open_id=sender_staff_id,
            text=text,
        )

    # ------------------------------------------------------------------
    # Link command detection
    # ------------------------------------------------------------------

    def is_link_command(self, raw: dict[str, Any]) -> bool:
        """Check if the message is a 'link <email>' keyword command."""
        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip().lower()
        return text.startswith("link ") or text.startswith("/link ") or text in ("link", "/link")

    def parse_link_email(self, raw: dict[str, Any]) -> str:
        """Extract email from a 'link alice@example.com' command. Returns '' if no email."""
        text_obj = raw.get("text") or {}
        text: str = text_obj.get("content", "").strip()
        if text.lower().startswith("/link"):
            text = text[5:].strip()
        elif text.lower().startswith("link"):
            text = text[4:].strip()
        if "@" in text:
            return text.strip().lower()
        return ""

    # ------------------------------------------------------------------
    # Outbound — card + message API calls
    # ------------------------------------------------------------------

    async def send_markdown(
        self, title: str, text: str, *, user_ids: list[str] | None = None
    ) -> str | None:
        """Send a proactive markdown message to specific users."""
        if not user_ids:
            logger.warning("[DingTalk] send_markdown called with no user_ids")
            return None

        url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload = {
            "msgParam": json.dumps({"title": title, "text": text}),
            "msgKey": "sampleMarkdown",
            "robotCode": self._bot_user_id,
            "userIds": user_ids,
        }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                result: str | None = resp.json().get("processQueryKey")
                return result
            logger.warning("[DingTalk] send_markdown failed: {}", resp.text)
            return None
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] send_markdown error")
            return None

    async def reply_markdown(
        self,
        title: str,
        text: str,
        *,
        open_conversation_id: str = "",
        user_id: str = "",
    ) -> str | None:
        """Reply to a conversation with markdown.

        For DM conversations, uses oToMessages/batchSend with userIds (since
        DingTalk's group endpoint rejects single-chat conversationIds).
        For group conversations, uses groupMessages/send with openConversationId.
        """
        cid = open_conversation_id or self._conversation_id
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        msg_param = json.dumps({"title": title, "text": text})
        effective_user_id = user_id or (self._sender_staff_id if self._is_dm else "")

        if effective_user_id:
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            payload = {
                "msgParam": msg_param,
                "msgKey": "sampleMarkdown",
                "robotCode": self._bot_user_id,
                "userIds": [effective_user_id],
            }
        else:
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            payload = {
                "msgParam": msg_param,
                "msgKey": "sampleMarkdown",
                "robotCode": self._bot_user_id,
                "openConversationId": cid,
            }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                result: str | None = resp.json().get("processQueryKey")
                return result
            logger.warning("[DingTalk] reply_markdown failed: {}", resp.text)
            return None
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] reply_markdown error")
            return None

    # ------------------------------------------------------------------
    # Interactive card operations
    # ------------------------------------------------------------------

    async def create_and_deliver_card(
        self,
        *,
        card_template_id: str,
        open_conversation_id: str,
        card_data: dict[str, Any],
        out_track_id: str,
    ) -> bool:
        """Create + deliver an interactive card instance."""
        url = "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "cardTemplateId": card_template_id,
            "outTrackId": out_track_id,
            "openConversationId": open_conversation_id,
            "callbackType": "STREAM",
            "cardData": {"cardParamMap": card_data},
        }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning("[DingTalk] create_and_deliver_card failed: {}", resp.text)
            return False
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] create_and_deliver_card error")
            return False

    async def streaming_update_card(
        self,
        *,
        out_track_id: str,
        guid: str,
        key: str,
        content: str,
        is_final: bool = False,
        is_error: bool = False,
    ) -> bool:
        """Stream-update a card variable. DingTalk requires PUT, not POST."""
        url = "https://api.dingtalk.com/v1.0/card/streaming"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "outTrackId": out_track_id,
            "guid": guid,
            "key": key,
            "content": content,
            "isFull": True,
            "isFinalize": is_final,
            "isError": is_error,
        }
        try:
            resp = await self._http.put(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                raise DingtalkRateLimitError("rate limited")
            logger.warning("[DingTalk] streaming_update failed: {}", resp.text)
            return False
        except DingtalkRateLimitError:
            raise
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] streaming_update error")
            return False

    async def update_card_actions(
        self,
        *,
        out_track_id: str,
        card_data: dict[str, Any],
    ) -> bool:
        """Update card data (buttons, status) via PUT."""
        url = "https://api.dingtalk.com/v1.0/card/instances"
        headers = {
            "x-acs-dingtalk-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": card_data},
        }
        try:
            resp = await self._http.put(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning("[DingTalk] update_card_actions failed: {}", resp.text)
            return False
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] update_card_actions error")
            return False

    # ------------------------------------------------------------------
    # Identity resolution (IdentityResolver protocol)
    # ------------------------------------------------------------------

    async def resolve_email(self, open_id: str) -> str | None:
        """Look up a DingTalk user's email by staffId."""
        url = "https://oapi.dingtalk.com/topapi/v2/user/get"
        params = {"access_token": self._access_token}
        payload = {"userid": open_id}
        try:
            resp = await self._http.post(url, params=params, json=payload)
            data = resp.json()
            if data.get("errcode") != 0:
                logger.warning("[DingTalk] user/get failed: {}", data)
                return None
            result = data.get("result", {})
            email = result.get("email") or result.get("org_email") or ""
            return email.strip().lower() if email else None
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] resolve_email error")
            return None

    # ------------------------------------------------------------------
    # Rejection notifier (RejectionNotifier protocol)
    # ------------------------------------------------------------------

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        """DingTalk session-webhook replies can't upload local media; the
        artifact dispatcher falls back to a share-link. Native file send needs
        the OpenAPI media-upload + message-send endpoints (out of scope)."""
        del local_path, filename, mime
        return False

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        """Send a rejection notice to the conversation."""
        return await self.reply_markdown(
            title="Notice",
            text=text,
            open_conversation_id=chat_id,
            user_id=self._sender_staff_id if self._is_dm else "",
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks (called by OutboundRunTailer on the connector)
    # ------------------------------------------------------------------

    async def on_processing_start(self, state: Any) -> None:
        pass

    async def on_processing_complete(self, state: Any) -> None:
        pass

    async def on_processing_failed(self, state: Any) -> None:
        pass
