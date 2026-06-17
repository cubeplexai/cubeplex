"""Slack outbound renderer — Block Kit messages with chat.update streaming."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.outbound import note_edit_success, note_flood_strike
from cubebox.im.slack.connector import SlackRateLimitError
from cubebox.im.types import RenderState

_SLACK_SECTION_LIMIT = 3000
_SPLIT_THRESHOLD = 2800


class SlackOpDispatcher:
    """Dispatches outbound ops to Slack via Block Kit messages."""

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

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = s.card_state.streaming_content
        if not text:
            text = "..."
        current_segment = text[self.sent_char_offset :]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = _find_split_point(current_segment, _SPLIT_THRESHOLD)
            send_text = current_segment[:split_at]
            self.sent_char_offset += split_at
        else:
            send_text = current_segment
        msg_ts = await self._connector.send_message(send_text)
        if msg_ts is None:
            return False
        s.card_id = msg_ts
        s.bot_message_id = msg_ts
        if s.inbound_message_id:
            await self._connector.add_reaction(s.inbound_message_id, "hourglass_flowing_sand")
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.bot_message_id is None:
            return await self.dispatch_create(state)
        full_content = s.card_state.streaming_content
        current_segment = full_content[self.sent_char_offset :]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = _find_split_point(current_segment, _SPLIT_THRESHOLD)
            finalize_text = current_segment[:split_at]
            try:
                await self._connector.edit_message(s.bot_message_id, finalize_text)
            except SlackRateLimitError:
                note_flood_strike(s)
                return False
            self.sent_char_offset += split_at
            remaining = full_content[self.sent_char_offset :]
            if remaining:
                msg_ts = await self._connector.send_message(remaining[:_SPLIT_THRESHOLD])
                if msg_ts:
                    s.card_id = msg_ts
                    s.bot_message_id = msg_ts
            note_edit_success(s)
            return True
        try:
            ok = await self._connector.edit_message(s.bot_message_id, current_segment)
        except SlackRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return bool(ok)

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
            await self._send_pending_input_buttons(pending)
            self._pending_input_sent_id = pending_id
        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.bot_message_id = None
            self.sent_char_offset = len(s.card_state.streaming_content)
        return True

    async def _send_pending_input_buttons(self, pending: Any) -> None:
        """Send AskUser/SandboxConfirm as Block Kit buttons."""
        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        short_qid = qid[:8]

        blocks: list[dict[str, Any]] = []
        if pending.question:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": pending.question},
                }
            )

        elements: list[dict[str, Any]] = []
        for label, value, btn_type in pending.choices:
            style: str | None = None
            if btn_type == "danger":
                style = "danger"
            elif btn_type != "default":
                style = "primary"
            action_id = f"im:{pending.kind}:{pending.run_id}:{short_qid}:{akey}:{value}"
            if len(action_id) > 255:
                action_id = action_id[:255]
            btn: dict[str, Any] = {
                "type": "button",
                "text": {"type": "plain_text", "text": label[:75]},
                "action_id": action_id,
            }
            if style:
                btn["style"] = style
            elements.append(btn)

        if elements:
            blocks.append({"type": "actions", "elements": elements})

        text = pending.question or "Please choose:"
        msg_ts = await self._connector.send_message_with_blocks(blocks, text=text)
        if msg_ts is None:
            notice = "_(Please continue in the cubebox web UI.)_"
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
            if s.inbound_message_id:
                await self._connector.remove_reaction(
                    s.inbound_message_id, "hourglass_flowing_sand"
                )
            return True
        remaining = full_content[self.sent_char_offset :]
        if s.bot_message_id is not None and len(remaining) <= _SLACK_SECTION_LIMIT:
            try:
                await self._connector.edit_message(s.bot_message_id, remaining)
            except Exception:
                logger.warning("[Slack] finalize edit failed", exc_info=True)
                await self.emergency_text(remaining[:4000])
        else:
            while remaining:
                chunk = remaining[:_SLACK_SECTION_LIMIT]
                remaining = remaining[_SLACK_SECTION_LIMIT:]
                if s.bot_message_id and not self.sent_char_offset:
                    try:
                        await self._connector.edit_message(s.bot_message_id, chunk)
                    except Exception:
                        await self._connector.send_message(chunk)
                else:
                    msg_ts = await self._connector.send_message(chunk)
                    if msg_ts:
                        s.bot_message_id = msg_ts
                self.sent_char_offset += len(chunk)
        if s.inbound_message_id:
            await self._connector.remove_reaction(s.inbound_message_id, "hourglass_flowing_sand")
            if not s.card_state.error:
                await self._connector.add_reaction(s.inbound_message_id, "white_check_mark")
            else:
                await self._connector.add_reaction(s.inbound_message_id, "x")
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector.send_message(text[:4000])
        except Exception:
            logger.warning("[Slack] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        pass


def _find_split_point(text: str, limit: int) -> int:
    """Find a line-boundary split point at or before ``limit``."""
    idx = text.rfind("\n", 0, limit)
    if idx > limit // 2:
        return idx + 1
    return limit
