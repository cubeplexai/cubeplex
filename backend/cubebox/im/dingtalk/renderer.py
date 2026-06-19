"""DingTalk outbound renderer — Interactive Card with streaming updates."""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from cubebox.im.dingtalk.connector import DingtalkRateLimitError
from cubebox.im.outbound import note_edit_success, note_flood_strike
from cubebox.im.types import RenderState


class DingtalkOpDispatcher:
    """Dispatches outbound ops to DingTalk via interactive cards."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        card_template_id: str,
        open_conversation_id: str,
    ) -> None:
        self._connector = connector
        self._state = state
        self._card_template_id = card_template_id
        self._open_conversation_id = open_conversation_id
        self._pending_input_sent_id: str | None = None
        self._stream_seq: int = 0

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = s.card_state.streaming_content or "..."
        out_track_id = f"cubebox-{s.run_id}-{uuid.uuid4().hex[:8]}"

        ok = await self._connector.create_and_deliver_card(
            card_template_id=self._card_template_id,
            open_conversation_id=self._open_conversation_id,
            card_data={"content": text, "status": "thinking"},
            out_track_id=out_track_id,
        )
        if ok:
            s.card_id = out_track_id
            return True
        s.card_unavailable = True
        await self._connector.reply_markdown(
            title="cubebox",
            text=text,
            open_conversation_id=self._open_conversation_id,
        )
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.card_unavailable:
            return True
        if s.card_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        self._stream_seq += 1
        guid = f"{s.card_id}-{self._stream_seq}"
        try:
            ok = await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=guid,
                key="content",
                content=full_content,
            )
        except DingtalkRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return bool(ok)

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        pending = s.card_state.pending_input
        pending_id = (
            f"{pending.kind}:{pending.run_id}:{pending.question_id or ''}" if pending else None
        )

        if (
            pending is not None
            and pending.resolved_choice is None
            and pending.choices
            and pending_id != self._pending_input_sent_id
        ):
            if s.card_unavailable or s.card_id is None:
                await self._emergency_pending_input(pending)
            else:
                await self._send_pending_input_buttons(pending)
            self._pending_input_sent_id = pending_id

        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.card_unavailable = False
            self._stream_seq = 0
        return True

    async def _emergency_pending_input(self, pending: Any) -> None:
        """Fallback: send HITL question as plain markdown."""
        text = pending.question or "Please continue in the cubebox web UI."
        if pending.choices:
            labels = ", ".join(label for label, _, _ in pending.choices)
            text = f"{text}\n\nOptions: {labels}\n\n_(Please answer in the web UI.)_"
        await self.emergency_text(text)

    async def _send_pending_input_buttons(self, pending: Any) -> None:
        """Update the card with action buttons for AskUser/SandboxConfirm."""
        s = self._state
        if s.card_id is None or s.card_unavailable:
            return

        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        short_qid = qid[:8]

        buttons: list[dict[str, Any]] = []
        for label, value, btn_type in pending.choices:
            action_id = f"im:{pending.kind}:{pending.run_id}:{short_qid}:{akey}:{value}"
            buttons.append(
                {
                    "label": label[:20],
                    "actionId": action_id,
                    "type": btn_type,
                }
            )

        card_data: dict[str, Any] = {
            "question": pending.question or "Please choose:",
            "buttons": buttons,
        }
        ok = await self._connector.update_card_actions(
            out_track_id=s.card_id,
            card_data=card_data,
        )
        if not ok:
            await self._emergency_pending_input(pending)

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = s.card_state.streaming_content or ""

        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (full_content + error_suffix) if full_content else error_suffix

        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(f"📎 [{a.name}]({a.share_url})" for a in artifacts if a.share_url)
            if links:
                full_content = f"{full_content}\n\n{links}" if full_content else links

        if s.card_id and not s.card_unavailable:
            status = "error" if s.card_state.error else "done"
            self._stream_seq += 1
            await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=f"{s.card_id}-{self._stream_seq}",
                key="content",
                content=full_content,
                is_final=True,
                is_error=bool(s.card_state.error),
            )
            await self._connector.update_card_actions(
                out_track_id=s.card_id,
                card_data={"status": status},
            )
        elif full_content:
            await self._connector.reply_markdown(
                title="cubebox",
                text=full_content[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.reply_markdown(
                title="cubebox",
                text=text[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        except Exception:
            logger.warning("[DingTalk] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        if self._connector._http_own is not None:
            await self._connector._http_own.aclose()
            self._connector._http_own = None
