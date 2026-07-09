"""DingTalk outbound renderer — AI Card with streaming updates."""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from cubebox.im.dingtalk.connector import DingtalkRateLimitError
from cubebox.im.outbound import note_edit_success, note_flood_strike
from cubebox.im.types import RenderState

AI_CARD_TEMPLATE_ID = "382e4302-551d-4880-bf29-a30acfab2e71.schema"
_CONTENT_KEY = "msgContent"

_FLOW_INPUTING = "2"
_FLOW_FINISHED = "3"
_FLOW_FAILED = "5"


def _ai_card_data(content: str, flow_status: str) -> dict[str, Any]:
    return {
        "flowStatus": flow_status,
        _CONTENT_KEY: content,
        "staticMsgContent": "",
        "sys_full_json_obj": json.dumps({"order": [_CONTENT_KEY]}),
        "config": json.dumps({"autoLayout": True}),
    }


class DingtalkOpDispatcher:
    """Dispatches outbound ops to DingTalk via AI Cards."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
        open_conversation_id: str,
    ) -> None:
        self._connector = connector
        self._state = state
        self._open_conversation_id = open_conversation_id
        self._pending_input_sent_id: str | None = None
        self._stream_seq: int = 0
        self._inputing_started: bool = False

    # ---- card lifecycle --------------------------------------------------

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        out_track_id = f"cubebox-{s.run_id}-{uuid.uuid4().hex[:8]}"

        ok = await self._connector.create_ai_card(
            card_template_id=AI_CARD_TEMPLATE_ID,
            out_track_id=out_track_id,
            open_conversation_id=self._open_conversation_id,
        )
        if ok:
            s.card_id = out_track_id
            return True
        s.card_unavailable = True
        return True

    async def _ensure_inputing(self) -> bool:
        """Transition card to INPUTING before the first streaming update."""
        if self._inputing_started:
            return True
        s = self._state
        if s.card_id is None or s.card_unavailable:
            return False
        content = s.card_state.streaming_content or ""
        ok = await self._connector.update_card_actions(
            out_track_id=s.card_id,
            card_data=_ai_card_data(content, _FLOW_INPUTING),
        )
        if ok:
            self._inputing_started = True
        return ok

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.card_unavailable:
            return True
        if s.card_id is None:
            return await self.dispatch_create(state)

        await self._ensure_inputing()

        full_content = s.card_state.streaming_content
        self._stream_seq += 1
        guid = f"{s.card_id}-{self._stream_seq}"
        try:
            ok = await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=guid,
                key=_CONTENT_KEY,
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
            await self._emergency_pending_input(pending)
            self._pending_input_sent_id = pending_id

        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.card_unavailable = False
            self._stream_seq = 0
            self._inputing_started = False
        return True

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
            await self._ensure_inputing()
            self._stream_seq += 1
            ok = await self._connector.streaming_update_card(
                out_track_id=s.card_id,
                guid=f"{s.card_id}-{self._stream_seq}",
                key=_CONTENT_KEY,
                content=full_content,
                is_final=True,
                is_error=bool(s.card_state.error),
            )
            if ok:
                flow = _FLOW_FAILED if s.card_state.error else _FLOW_FINISHED
                await self._connector.update_card_actions(
                    out_track_id=s.card_id,
                    card_data=_ai_card_data(full_content, flow),
                    card_update_options={"updateCardDataByKey": True},
                )
            else:
                await self._connector.reply_markdown(
                    title="cubebox",
                    text=full_content[:4000],
                    open_conversation_id=self._open_conversation_id,
                )
        elif full_content:
            await self._connector.reply_markdown(
                title="cubebox",
                text=full_content[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        return True

    # ---- fallbacks -------------------------------------------------------

    async def _emergency_pending_input(self, pending: Any) -> None:
        text = pending.question or "Please continue in the cubebox web UI."
        if pending.choices:
            labels = ", ".join(label for label, _, _ in pending.choices)
            text = f"{text}\n\nOptions: {labels}\n\n_(Please answer in the web UI.)_"
        await self.emergency_text(text)

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.reply_markdown(
                title="cubebox",
                text=text[:4000],
                open_conversation_id=self._open_conversation_id,
            )
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] emergency text send failed")

    async def aclose(self) -> None:
        if self._connector._http_own is not None:
            await self._connector._http_own.aclose()
            self._connector._http_own = None
