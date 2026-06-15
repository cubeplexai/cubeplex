"""Feishu-specific OpDispatcher -- wraps CardKit calls.

Extracted from OutboundRunTailer._dispatch_op so the tailer no longer
hard-codes Feishu rendering logic.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.outbound import _FloodSignal, note_edit_success, note_flood_strike
from cubebox.im.types import RenderState


class FeishuOpDispatcher:
    """Dispatches outbound ops to Feishu CardKit."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        cardkit: Any,
    ) -> None:
        self._connector = connector
        self._state = state
        self._cardkit = cardkit

    async def dispatch_create(self, state: RenderState) -> bool:
        """Create a CardKit entity and send the init message."""
        cardkit = self._cardkit
        if cardkit is None:
            return False
        if state.card_unavailable:
            return False

        from cubebox.im.feishu.card_renderer import render

        card_json = render(state.card_state)
        try:
            card_id = await cardkit.create_entity(card_json)
        except Exception:
            logger.warning(
                "[outbound] CardKit create_entity failed; engaging emergency text",
                exc_info=True,
            )
            state.card_unavailable = True
            await self._emergency_card_create_fallback(state)
            return False
        state.card_id = card_id
        state.card_state.advance_seq()
        try:
            msg_id = await self._connector.send_card_init_message(card_id)
        except Exception:
            logger.warning("[outbound] send_card_init_message raised", exc_info=True)
            msg_id = None
        state.bot_message_id = msg_id
        if msg_id is None:
            # The CardKit entity exists but no IM bubble points at it --
            # subsequent stream/patch ops would update an invisible card.
            # Disable card path, fall back to emergency text so the user
            # at least sees the partial answer.
            logger.warning(
                "[outbound] send_card_init_message returned no message_id; engaging emergency text"
            )
            state.card_unavailable = True
            await self._emergency_card_create_fallback(state)
            return False
        return True

    async def dispatch_stream(self, state: RenderState, text: str) -> bool:
        """Push a streaming text update to CardKit."""
        cardkit = self._cardkit
        if cardkit is None:
            return False
        if state.card_id is None or state.card_unavailable:
            return False
        seq = state.card_state.advance_seq()
        from cubebox.im.feishu.card_renderer import (
            optimize_markdown_style as _optimize,
        )

        sanitized = _optimize(text, citation_index=state.card_state.citation_index)
        try:
            await cardkit.stream_text(
                card_id=state.card_id,
                element_id="streaming_content",
                content=sanitized,
                sequence=seq,
            )
            note_edit_success(state)
            return True
        except _FloodSignal:
            note_flood_strike(state)
            return False
        except Exception:
            logger.warning("[outbound] stream_text failed", exc_info=True)
            return False

    async def dispatch_patch(self, state: RenderState) -> bool:
        """Send a full-card patch to CardKit."""
        cardkit = self._cardkit
        if cardkit is None:
            return False
        if state.card_id is None or state.card_unavailable:
            return False

        from cubebox.im.feishu.card_renderer import render

        seq = state.card_state.advance_seq()
        try:
            await cardkit.patch_card(
                card_id=state.card_id,
                card_json=render(state.card_state),
                sequence=seq,
            )
            note_edit_success(state)
            return True
        except _FloodSignal:
            # Coalesce -- next event will rebuild and resend. Count the
            # strike so a sustained tool-heavy run that's getting
            # throttled trips ``edits_disabled`` and stops hammering
            # CardKit through 230020 responses.
            note_flood_strike(state)
            await self._maybe_surface_pending_via_emergency(state)
            return False
        except Exception:
            logger.warning("[outbound] patch_card failed", exc_info=True)
            await self._maybe_surface_pending_via_emergency(state)
            return False

    async def dispatch_finalize(self, state: RenderState) -> bool:
        """Send the final card update to CardKit."""
        cardkit = self._cardkit
        if cardkit is None:
            return False
        if state.card_id is None or state.card_unavailable:
            if state.card_state.error:
                await self.emergency_text(f"⚠️ {state.card_state.error}")
            elif state.card_state.streaming_content:
                await self.emergency_text(state.card_state.streaming_content[:4000])
            return False

        from cubebox.im.feishu.card_renderer import render

        seq = state.card_state.advance_seq()
        try:
            delivered = bool(
                await cardkit.finalize(
                    card_id=state.card_id,
                    card_json=render(state.card_state),
                    sequence=seq,
                )
            )
        except Exception:
            # ``cardkit.finalize`` is contracted to return False on its own
            # retry exhaustion, but a token-provider exception in
            # ``_headers()`` or a JSON decode failure escapes that contract.
            # Without this guard the tailer's lifecycle wrapper catches the
            # exception and only fires the failed-hook reaction; the buffered
            # final answer never reaches the user. Treat as
            # delivered=False so the emergency-text fallback below runs.
            logger.warning(
                "[outbound] cardkit.finalize raised; falling back to emergency text",
                exc_info=True,
            )
            delivered = False
        if not delivered:
            # CardKit finalize gave up after its retry budget (~2.5min).
            # The card stays half-rendered without an answer; surface
            # whatever we have as emergency text so the user at least
            # sees the response.
            logger.warning(
                "[outbound] CardKit finalize gave up for card_id={};"
                " surfacing answer via emergency text",
                state.card_id,
            )
            if state.card_state.error:
                await self.emergency_text(f"⚠️ {state.card_state.error}")
            elif state.card_state.streaming_content:
                await self.emergency_text(state.card_state.streaming_content[:4000])
        return delivered

    async def emergency_text(self, text: str) -> None:
        """Send a plain-text emergency message via the connector."""
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[outbound] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        """Release the CardKit HTTP/2 connection pool."""
        aclose_fn = getattr(self._cardkit, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception:
                logger.warning("[outbound] cardkit.aclose() raised", exc_info=True)

    # -- private helpers --

    async def _maybe_surface_pending_via_emergency(self, state: RenderState) -> None:
        """Surface the HITL prompt via emergency text when patch_card cannot
        deliver it.

        The Feishu user is stranded otherwise: paused-HITL ``done`` is now
        non-terminal (round 2), so there is no later ``finalize`` to render
        the question -- the card stays at the pre-pending state forever.
        Fires at most once per question_id so a long flood-throttled HITL
        pause doesn't spam the same prompt with every retry.
        """
        pending = state.card_state.pending_input
        if pending is None or pending.resolved_choice is not None:
            return
        qid = pending.question_id or ""
        if not qid or state.pending_prompt_emergency_sent_qid == qid:
            return
        state.pending_prompt_emergency_sent_qid = qid
        kind_label = "❓ 待用户输入" if pending.kind == "ask_user" else "❓ 待沙箱操作确认"
        await self.emergency_text(
            f"{kind_label}\n\n{pending.question}\n\n"
            f"_(卡片更新暂时不可用；请在 cubebox 网页端继续。)_"[:4000]
        )

    async def _emergency_card_create_fallback(self, state: RenderState) -> None:
        """Best-effort plain-text rescue when CardKit create or card-init
        fails.

        Always sends the generic unavailability notice; then surfaces whatever
        meaningful state we already have so the user is not stranded.
        """
        card_state = state.card_state
        await self.emergency_text("⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示")
        if card_state.streaming_content:
            await self.emergency_text(card_state.streaming_content[:4000])
        pending = card_state.pending_input
        if pending is not None and pending.resolved_choice is None:
            kind_label = "❓ 待用户输入" if pending.kind == "ask_user" else "❓ 待沙箱操作确认"
            await self.emergency_text(
                f"{kind_label}\n\n{pending.question}\n\n_(请在 cubebox 网页端继续。)_"[:4000]
            )
