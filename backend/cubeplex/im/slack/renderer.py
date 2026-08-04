"""Slack outbound renderer — Block Kit messages with chat.update streaming."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.im.outbound import find_split_point, note_edit_success, note_flood_strike
from cubeplex.im.slack.connector import SlackRateLimitError
from cubeplex.im.types import RenderState

_SLACK_SECTION_LIMIT = 3000
_SPLIT_THRESHOLD = 2800


def _active_stream_text(card_state: Any) -> str:
    """Text currently being streamed to Slack.

    After HITL resolves, cubepi continues into ``post_hitl_content`` while
    the pre-HITL answer stays in ``streaming_content``. Slack posts the
    follow-up as a *new* message under the buttons, so we stream only the
    post-HITL buffer (DingTalk concatenates; Slack/Discord reset the message).
    """
    if getattr(card_state, "hitl_resolved", False):
        return str(card_state.post_hitl_content or "")
    return str(card_state.streaming_content or "")


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
        # Only reset the bot message once per resolved HITL question — later
        # patch_card events (tools/artifacts) still have resolved_choice set.
        self._hitl_reset_qid: str | None = None

    async def dispatch_create(self, state: Any) -> bool:
        s = self._state
        text = _active_stream_text(s.card_state)
        if not text:
            # After HITL, wait for real post_hitl text — a permanent "..." next
            # to ✅ is worse than silence until tokens (or empty finalize).
            if getattr(s.card_state, "hitl_resolved", False):
                return False
            text = "..."
        current_segment = text[self.sent_char_offset :]
        if not current_segment:
            return False
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            send_text = current_segment[:split_at]
            self.sent_char_offset += split_at
        else:
            send_text = current_segment
        msg_ts = await self._connector.send_message(send_text)
        if msg_ts is None:
            return False
        s.card_id = msg_ts
        s.bot_message_id = msg_ts
        # Processing hourglass is added in SlackConnector.on_processing_start
        # (tailer start), not here — waiting until first content made it feel late.
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        s = self._state
        if s.bot_message_id is None:
            return await self.dispatch_create(state)
        full_content = _active_stream_text(s.card_state)
        # Always paint the full cumulative buffer for the current message
        # (offset only advances when we split a long answer into multiple
        # Slack messages). Using only the tail after a coalesce could leave
        # earlier chunks missing if create posted a short prefix.
        current_segment = full_content[self.sent_char_offset :]
        if len(current_segment) > _SPLIT_THRESHOLD:
            split_at = find_split_point(current_segment, _SPLIT_THRESHOLD)
            finalize_text = current_segment[:split_at]
            try:
                await self._connector.edit_message(s.bot_message_id, finalize_text)
            except SlackRateLimitError:
                note_flood_strike(s)
                return False
            self.sent_char_offset += split_at
            remaining = full_content[self.sent_char_offset :]
            if remaining:
                # New message for the next segment. Keep offset at the start of
                # this segment so later stream edits repaint the full cumulative
                # text (same pattern as Discord/Teams). Advancing past the
                # remainder would make the next chat.update replace the new
                # message with only the delta suffix and drop prior text.
                posted = remaining[:_SLACK_SECTION_LIMIT]
                msg_ts = await self._connector.send_message(posted)
                if msg_ts:
                    s.card_id = msg_ts
                    s.bot_message_id = msg_ts
            note_edit_success(s)
            return True
        # After a split (or a stream tick with no new chars), segment can be
        # empty — skip the edit rather than posting invalid empty blocks.
        if not current_segment:
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
            # New Slack message for the post-HITL answer — only on the first
            # resolved patch for this question_id. Later tool/artifact patches
            # still carry resolved_choice and must not clear bot_message_id
            # again (that orphaned the partial reply / "..." placeholder).
            rid = f"{pending.kind}:{pending.run_id}:{pending.question_id or ''}"
            if rid != self._hitl_reset_qid:
                s.card_id = None
                s.bot_message_id = None
                # post_hitl_content is cumulative across multiple HITL turns in
                # one run. Pin offset to the current buffer end so the next
                # message only paints text produced after this resolution
                # (avoids reposting the first continuation on the second HITL).
                self.sent_char_offset = len(str(s.card_state.post_hitl_content or ""))
                self._hitl_reset_qid = rid
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
            notice = "_(Please continue in the CubePlex web UI.)_"
            await self._connector.send_message(f"{text}\n\n{notice}")

    async def dispatch_finalize(self, state: Any) -> bool:
        s = self._state
        full_content = _active_stream_text(s.card_state)
        if s.card_state.error:
            error_suffix = f"\n\n⚠️ {s.card_state.error}"
            full_content = (full_content + error_suffix) if full_content else error_suffix
        artifacts = s.card_state.artifacts
        if artifacts:
            links = "\n".join(f"📎 [{a.name}]({a.share_url})" for a in artifacts if a.share_url)
            if links:
                full_content = f"{full_content}\n\n{links}" if full_content else links
        if full_content:
            remaining = full_content[self.sent_char_offset :]
            if s.bot_message_id is not None and len(remaining) <= _SLACK_SECTION_LIMIT:
                try:
                    await self._connector.edit_message(s.bot_message_id, remaining)
                except Exception:
                    logger.opt(exception=True).warning("[Slack] finalize edit failed")
                    await self.emergency_text(remaining[:4000])
            else:
                # After a mid-stream split, offset is nonzero but bot_message_id
                # still holds the *current* segment — first chunk must edit that
                # message, not re-post the whole remaining buffer as new msgs.
                edit_current = s.bot_message_id is not None
                while remaining:
                    chunk = remaining[:_SLACK_SECTION_LIMIT]
                    remaining = remaining[_SLACK_SECTION_LIMIT:]
                    if edit_current and s.bot_message_id:
                        try:
                            await self._connector.edit_message(s.bot_message_id, chunk)
                        except Exception:
                            await self._connector.send_message(chunk)
                        edit_current = False
                    else:
                        msg_ts = await self._connector.send_message(chunk)
                        if msg_ts:
                            s.bot_message_id = msg_ts
                    self.sent_char_offset += len(chunk)
        elif s.bot_message_id is not None:
            # Empty final text but a "..." (or stale) bot message exists — e.g.
            # post-HITL tool events before done. Replace so it is not left forever.
            try:
                await self._connector.edit_message(s.bot_message_id, "✓")
            except Exception:
                logger.opt(exception=True).warning("[Slack] finalize placeholder clear failed")
        # Always clear processing markers — including empty post-HITL success
        # (HITL answer then done with no further text) so the hourglass is
        # not left hanging without a white_check_mark.
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
            logger.opt(exception=True).warning("[Slack] emergency text send failed")

    async def aclose(self) -> None:
        pass
