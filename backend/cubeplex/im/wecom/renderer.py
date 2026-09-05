"""WeCom cumulative stream renderer."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from loguru import logger

from cubeplex.im.types import RenderState

_STREAM_BYTE_LIMIT = 20_480
_STREAM_MAX_AGE_SECONDS = 330.0
_TRUNCATION_MARKER = "\n\n[Truncated — view the full response in CubePlex]"
_EXPIRED_CODES = frozenset({846604, 846608})


def _visible_text(state: RenderState) -> str:
    card = state.card_state
    parts: list[str] = []
    if card.streaming_content:
        parts.append(card.streaming_content)
    if card.post_hitl_content:
        parts.append(card.post_hitl_content)
    if card.error:
        parts.append(f"⚠️ {card.error}")
    links = [f"📎 [{item.name}]({item.share_url})" for item in card.artifacts if item.share_url]
    if links:
        parts.append("\n".join(links))
    pending = card.pending_input
    if pending is not None and pending.resolved_choice is None:
        prompt = pending.question or "Your input is required."
        parts.append(f"{prompt}\n\nContinue in the CubePlex web UI.")
    return "\n\n---\n\n".join(parts)


def _truncate_utf8(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= _STREAM_BYTE_LIMIT:
        return text
    marker = _TRUNCATION_MARKER.encode("utf-8")
    prefix = encoded[: _STREAM_BYTE_LIMIT - len(marker)].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER


def _error_code(response: dict[str, Any] | None) -> int:
    if response is None:
        return 0
    source = response
    if "errcode" not in source:
        body = response.get("body")
        source = body if isinstance(body, dict) else {}
    try:
        return int(source.get("errcode") or 0)
    except (TypeError, ValueError):
        return -1


class WecomOpDispatcher:
    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._connector = connector
        self._state = state
        self._now = now or time.monotonic
        self._stream_id: str | None = None
        self._started_at: float | None = None
        self._passive_expired = False

    async def _proactive_final(self, text: str) -> bool:
        return bool(await self._connector.send_proactive_text(text))

    async def dispatch_create(self, state: Any) -> bool:
        del state
        if self._stream_id is None:
            self._stream_id = uuid.uuid4().hex
            self._state.card_id = self._stream_id
        if self._started_at is None:
            self._started_at = self._now()
        if not self._state.reply_to_id:
            return True
        content = _truncate_utf8(_visible_text(self._state) or "Thinking…")
        response = await self._connector.send_stream(
            stream_id=self._stream_id,
            content=content,
            final=False,
        )
        if _error_code(response) in _EXPIRED_CODES:
            self._passive_expired = True
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        del state, text
        if not self._state.reply_to_id:
            return True
        if self._stream_id is None:
            return await self.dispatch_create(self._state)
        response = await self._connector.send_stream(
            stream_id=self._stream_id,
            content=_truncate_utf8(_visible_text(self._state)),
            final=False,
        )
        if _error_code(response) in _EXPIRED_CODES:
            self._passive_expired = True
        return True

    async def dispatch_patch(self, state: Any) -> bool:
        return await self.dispatch_stream(state, _visible_text(self._state))

    async def dispatch_finalize(self, state: Any) -> bool:
        del state
        final_text = _truncate_utf8(_visible_text(self._state))
        now = self._now()
        too_old = self._started_at is not None and now - self._started_at >= _STREAM_MAX_AGE_SECONDS
        if not self._state.reply_to_id or self._passive_expired or too_old:
            return await self._proactive_final(final_text)
        if self._stream_id is None:
            self._stream_id = uuid.uuid4().hex
            self._state.card_id = self._stream_id
        response = await self._connector.send_stream(
            stream_id=self._stream_id,
            content=final_text,
            final=True,
        )
        if response is not None and response.get("proactive_required"):
            return await self._proactive_final(final_text)
        code = _error_code(response)
        if code in _EXPIRED_CODES:
            return await self._proactive_final(final_text)
        return code in {0, 6000}

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.send_proactive_text(_truncate_utf8(text))
        except Exception:
            logger.opt(exception=True).warning("[WeCom] emergency text send failed")

    async def aclose(self) -> None:
        return None
