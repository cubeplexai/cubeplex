"""WeCom callback normalization and bound message operations."""

from __future__ import annotations

import re
from typing import Any, cast

from cubeplex.im.types import (
    DM_SCOPE_KEY,
    BindingMode,
    InboundEvent,
    make_channel_scope,
    make_participant_scope,
)

CALLBACK_COMMANDS = frozenset({"aibot_msg_callback", "aibot_callback"})
PROACTIVE_TEXT_LIMIT = 4000


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_text(body: dict[str, Any]) -> str:
    msgtype = str(body.get("msgtype") or "").lower()
    parts: list[str] = []
    if msgtype == "mixed":
        items = _mapping(body.get("mixed")).get("msg_item")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and str(item.get("msgtype") or "").lower() == "text":
                content = str(_mapping(item.get("text")).get("content") or "").strip()
                if content:
                    parts.append(content)
    elif msgtype == "text":
        content = str(_mapping(body.get("text")).get("content") or "").strip()
        if content:
            parts.append(content)
    elif msgtype == "voice":
        content = str(_mapping(body.get("voice")).get("content") or "").strip()
        if content:
            parts.append(content)

    if parts:
        return "\n".join(parts)

    quote = _mapping(body.get("quote"))
    quote_type = str(quote.get("msgtype") or "").lower()
    if quote_type in {"text", "voice"}:
        return str(_mapping(quote.get(quote_type)).get("content") or "").strip()
    return ""


def split_proactive_text(text: str, limit: int = PROACTIVE_TEXT_LIMIT) -> list[str]:
    """Split proactive Markdown without exceeding WeCom's character limit."""
    remaining = text
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = max(remaining.rfind("\n", 0, limit + 1), remaining.rfind(" ", 0, limit + 1))
        if split_at <= 0:
            split_at = limit
        else:
            split_at += 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return chunks


class WecomConnector:
    def __init__(
        self,
        *,
        bot_id: str = "",
        gateway: Any = None,
        chat_id: str | None = None,
        reply_req_id: str | None = None,
    ) -> None:
        self._bot_id = bot_id
        self._gateway = gateway
        self._chat_id = chat_id
        self._reply_req_id = reply_req_id

    async def send_stream(
        self,
        *,
        stream_id: str,
        content: str,
        final: bool,
    ) -> dict[str, Any] | None:
        if self._gateway is None or not self._reply_req_id:
            return None
        return cast(
            dict[str, Any],
            await self._gateway.send_passive(
                self._reply_req_id,
                {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "content": content, "finish": final},
                },
                final=final,
                skip_if_pending=not final,
            ),
        )

    async def send_proactive_text(self, text: str) -> str | None:
        if not self._chat_id:
            return None
        return await self.send_to_chat(self._chat_id, None, text)

    def parse_inbound(
        self,
        raw: dict[str, Any],
        *,
        binding_mode: BindingMode = "isolated",
    ) -> InboundEvent | None:
        if str(raw.get("cmd") or "") not in CALLBACK_COMMANDS:
            return None
        headers = _mapping(raw.get("headers"))
        body = _mapping(raw.get("body"))
        req_id = str(headers.get("req_id") or "").strip()
        msg_id = str(body.get("msgid") or "").strip()
        sender_id = str(_mapping(body.get("from")).get("userid") or "").strip()
        if not req_id or not msg_id or not sender_id:
            return None
        if self._bot_id and sender_id == self._bot_id:
            return None

        text = _extract_text(body)
        if not text:
            return None
        is_group = str(body.get("chattype") or "").lower() == "group"
        chat_id = str(body.get("chatid") or sender_id).strip()
        if not chat_id:
            return None
        if is_group:
            text = re.sub(r"^@\S+\s*", "", text).strip()
            if not text:
                return None
            if binding_mode == "shared":
                scope_key = make_channel_scope()
                scope_kind = "channel"
            else:
                scope_key = make_participant_scope(sender_id)
                scope_kind = "group"
        else:
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"

        return InboundEvent(
            platform="wecom",
            account_external_id=self._bot_id,
            platform_event_id=msg_id,
            channel_id=chat_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=req_id,
            inbound_message_id=msg_id,
            sender_ref=sender_id,
            sender_open_id=sender_id,
            text=text,
        )

    async def send_to_chat(
        self,
        chat_id: str,
        reply_to_id: str | None,
        text: str,
    ) -> str | None:
        if self._gateway is None or not chat_id or not text:
            return None
        if reply_to_id:
            await self._gateway.send_passive(
                reply_to_id,
                {"msgtype": "markdown", "markdown": {"content": text[:PROACTIVE_TEXT_LIMIT]}},
                final=True,
                skip_if_pending=False,
            )
            return reply_to_id

        sent_id: str | None = None
        for chunk in split_proactive_text(text):
            response = await self._gateway.send_proactive(
                chat_id,
                {"msgtype": "markdown", "markdown": {"content": chunk}},
            )
            headers = _mapping(response.get("headers"))
            candidate = str(headers.get("req_id") or "").strip()
            if candidate:
                sent_id = candidate
        return sent_id

    async def on_processing_start(self, state: Any) -> None:
        del state

    async def on_processing_complete(self, state: Any) -> None:
        del state

    async def on_processing_failed(self, state: Any) -> None:
        del state

    async def upload_image(self, local_path: str) -> str | None:
        del local_path
        return None

    async def send_image(self, *, local_path: str, filename: str) -> bool:
        del local_path, filename
        return False

    async def send_file(self, *, local_path: str, filename: str, mime: str | None) -> bool:
        del local_path, filename, mime
        return False
