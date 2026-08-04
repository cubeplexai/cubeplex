"""Teams outbound renderer — Markdown messages with updateActivity streaming.

Web Chat / Direct Line do not support PUT updateActivity (405). For those
channels we buffer until finalize and send complete message(s).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.im.outbound import find_split_point, note_edit_success, note_flood_strike
from cubeplex.im.teams.connector import TEAMS_MSG_LIMIT, TeamsRateLimitError
from cubeplex.im.types import RenderState

_SPLIT_THRESHOLD = 24000


class TeamsOpDispatcher:
    """Dispatches outbound ops to Teams via Markdown messages + Adaptive Cards."""

    def __init__(
        self,
        *,
        connector: Any,
        state: RenderState,
    ) -> None:
        self._connector = connector
        self._state = state
        self.sent_char_offset: int = 0
        self._pending_input_sent_id: str | None = None

    def _can_edit(self) -> bool:
        return bool(getattr(self._connector, "supports_message_edit", True))

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        # No updateActivity → don't post a partial first token; finalize will
        # send the full reply once (avoids a stuck "你好" with no follow-up).
        if not self._can_edit():
            return True
        text = s.card_state.streaming_content
        if not text:
            text = "..."
        current_segment = text[self.sent_char_offset :]
        sealed_prefix = False
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            send_text = current_segment[:split_at]
            self.sent_char_offset += split_at
            sealed_prefix = True
        else:
            send_text = current_segment
        msg_id = await self._connector.send_message(send_text)
        if msg_id is None:
            return False
        s.card_id = msg_id
        s.bot_message_id = msg_id
        # Partial create: seal this message so drain/stream opens a new one.
        if sealed_prefix:
            s.card_id = None
            s.bot_message_id = None
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        # Buffer only; finalize posts the complete text on non-edit channels.
        if not self._can_edit():
            return True
        if s.bot_message_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        current_segment = full_content[self.sent_char_offset :]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            finalize_text = current_segment[:split_at]
            try:
                ok = await self._connector.edit_message(s.bot_message_id, finalize_text)
            except TeamsRateLimitError:
                note_flood_strike(s)
                return False
            if not ok:
                # Channel rejected edit mid-stream — fall back to send-only.
                return True
            self.sent_char_offset += split_at
            remaining = full_content[self.sent_char_offset :]
            if remaining:
                msg_id = await self._connector.send_message(remaining[:_SPLIT_THRESHOLD])
                if msg_id:
                    s.card_id = msg_id
                    s.bot_message_id = msg_id
            note_edit_success(s)
            return True
        try:
            ok = await self._connector.edit_message(s.bot_message_id, current_segment)
        except TeamsRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        # False (e.g. 405) is OK: finalize will send the full buffer.
        return True

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        pending = s.card_state.pending_input
        pending_id = f"{pending.kind}:{pending.run_id}" if pending else None
        if (
            pending is not None
            and pending.resolved_choice is None
            and pending.choices
            and pending_id != self._pending_input_sent_id
        ):
            await self._send_pending_input_card(pending)
            self._pending_input_sent_id = pending_id
        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.bot_message_id = None
            self.sent_char_offset = len(s.card_state.streaming_content)
        return True

    async def _send_pending_input_card(self, pending: Any) -> None:
        """Send AskUser/SandboxConfirm as an Adaptive Card with Action.Submit."""
        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        short_qid = qid[:8]

        body: list[dict[str, Any]] = []
        if pending.question:
            body.append(
                {
                    "type": "TextBlock",
                    "text": pending.question,
                    "wrap": True,
                }
            )

        actions: list[dict[str, Any]] = []
        for label, value, btn_type in pending.choices:
            style = "destructive" if btn_type == "danger" else "default"
            action_data = f"im:{pending.kind}:{pending.run_id}:{short_qid}:{akey}:{value}"
            actions.append(
                {
                    "type": "Action.Submit",
                    "title": label[:40],
                    "style": style,
                    "data": {"action": action_data},
                }
            )

        card: dict[str, Any] = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
            "actions": actions,
        }
        msg_id = await self._connector.send_card(card)
        if msg_id is None:
            text = pending.question or "Please choose:"
            notice = "_(Please continue in the CubePlex web UI.)_"
            await self._connector.send_message(f"{text}\n\n{notice}")

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = s.card_state.streaming_content
        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (full_content + error_suffix) if full_content else error_suffix
        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(f"📎 [{a.name}]({a.share_url})" for a in artifacts if a.share_url)
            if links:
                full_content = f"{full_content}\n\n{links}" if full_content else links
        if not full_content:
            return True
        remaining = full_content[self.sent_char_offset :]
        if not remaining:
            return True

        # Prefer edit when the channel supports it and we already have a msg.
        if s.bot_message_id is not None and self._can_edit() and len(remaining) <= TEAMS_MSG_LIMIT:
            try:
                ok = await self._connector.edit_message(s.bot_message_id, remaining)
            except Exception:
                logger.opt(exception=True).warning("[Teams] finalize edit failed")
                ok = False
            if ok:
                self.sent_char_offset += len(remaining)
                return True
            # Fall through to send-new-message path.

        # Send-only (Web Chat / edit failed / oversize): post remaining chunks.
        while remaining:
            chunk = remaining[:TEAMS_MSG_LIMIT]
            remaining = remaining[TEAMS_MSG_LIMIT:]
            msg_id = await self._connector.send_message(chunk)
            if msg_id:
                s.bot_message_id = msg_id
            self.sent_char_offset += len(chunk)
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.send_message(text[:TEAMS_MSG_LIMIT])
        except Exception:
            logger.opt(exception=True).warning("[Teams] emergency text send failed")

    async def aclose(self) -> None:
        pass
