"""Discord outbound renderer — plain Markdown message edits.

Unlike Feishu's CardKit pipeline, Discord rendering is straightforward:
send one message, edit it as content accumulates, split at 2000 chars.

Discord reuses the shared ``RenderState`` from ``im/types.py`` (which
carries card_id, run_id, edits_disabled, stream_interval, etc. — all
fields that ``fold_event`` accesses). Discord-specific fields
(``sent_char_offset``) are added directly to this module's dispatcher
state rather than subclassing RenderState.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubebox.im.discord.connector import DiscordRateLimitError
from cubebox.im.outbound import note_edit_success, note_flood_strike
from cubebox.im.types import RenderState

_DISCORD_MSG_LIMIT = 2000
_SPLIT_THRESHOLD = 1900


class DiscordOpDispatcher:
    """Dispatches outbound ops to Discord via message send/edit."""

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
        msg_id = await self._connector.send_message(send_text)
        if msg_id is None:
            return False
        s.card_id = msg_id
        s.bot_message_id = msg_id
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
            except DiscordRateLimitError:
                note_flood_strike(s)
                return False
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
        except DiscordRateLimitError:
            note_flood_strike(s)
            return False
        if ok:
            note_edit_success(s)
        return bool(ok)

    async def dispatch_patch(self, state: Any) -> bool:
        s = self._state
        # Render AskUser/SandboxConfirm buttons when pending_input is set.
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
        # When the user answers a pending input, reset card state so the
        # follow-up reply appears as a NEW message below the buttons
        # instead of being edited into the old message above them.
        if pending is not None and pending.resolved_choice is not None:
            s.card_id = None
            s.bot_message_id = None
            self.sent_char_offset = len(s.card_state.streaming_content)
        # Send typing indicator during tool calls.
        if getattr(self._connector, "_bot", None) is not None and s.bot_message_id is not None:
            try:
                channel = self._connector._bot.get_channel(int(self._connector._channel_id or "0"))
                if channel is not None:
                    await channel.typing()
            except Exception:
                pass
        return True

    async def _send_pending_input_buttons(self, pending: Any) -> None:
        """Send AskUser/SandboxConfirm as Discord buttons."""
        try:
            import discord
        except ImportError:
            return

        view = discord.ui.View(timeout=600)
        qid = pending.question_id or ""
        akey = pending.answer_key or ""
        # Discord custom_id max is 100 chars.  Full question_id can be
        # 33+ chars; combined with run_id (36) and answer_key the id
        # overflows, truncating value and making all buttons identical
        # (→ 400).  Cap qid to 8 chars; the interaction handler loads
        # the full question_id from the DB pending.
        short_qid = qid[:8]
        for label, value, btn_type in pending.choices:
            style = discord.ButtonStyle.primary
            if btn_type == "danger":
                style = discord.ButtonStyle.danger
            elif btn_type == "default":
                style = discord.ButtonStyle.secondary
            cid = f"im:{pending.kind}:{pending.run_id}:{short_qid}:{akey}:{value}"
            if len(cid) > 100:
                cid = cid[:100]
            button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
                label=label,
                style=style,
                custom_id=cid,
            )
            view.add_item(button)
        text = pending.question or "请选择："
        send_with_view = getattr(self._connector, "send_message_with_view", None)
        msg_id = None
        if send_with_view is not None:
            msg_id = await send_with_view(text, view)
        if msg_id is None:
            notice = "_(请在 cubebox 网页端继续。)_"
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
                await self._connector.remove_reaction(s.inbound_message_id, "⏳")
            return True
        remaining = full_content[self.sent_char_offset :]
        if s.bot_message_id is not None and len(remaining) <= _DISCORD_MSG_LIMIT:
            try:
                await self._connector.edit_message(s.bot_message_id, remaining)
            except Exception:
                logger.warning("[Discord] finalize edit failed", exc_info=True)
                await self.emergency_text(remaining[:4000])
        else:
            while remaining:
                chunk = remaining[:_DISCORD_MSG_LIMIT]
                remaining = remaining[_DISCORD_MSG_LIMIT:]
                if s.bot_message_id and not self.sent_char_offset:
                    try:
                        await self._connector.edit_message(s.bot_message_id, chunk)
                    except Exception:
                        await self._connector.send_message(chunk)
                else:
                    msg_id = await self._connector.send_message(chunk)
                    if msg_id:
                        s.bot_message_id = msg_id
                self.sent_char_offset += len(chunk)
        if s.inbound_message_id:
            await self._connector.remove_reaction(s.inbound_message_id, "⏳")
        return True

    async def emergency_text(self, text: str) -> None:
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[Discord] emergency text send failed", exc_info=True)

    async def aclose(self) -> None:
        pass


def _find_split_point(text: str, limit: int) -> int:
    """Find a line-boundary split point at or before ``limit``."""
    idx = text.rfind("\n", 0, limit)
    if idx > limit // 2:
        return idx + 1
    return limit
