"""Shared ``/new`` / ``/reset`` command parse + apply for all IM platforms.

Platform ingress must intercept these BEFORE ``ingest_inbound_event`` —
otherwise the message is routed to the agent as ordinary text and never
rotates the conversation binding.

The apply path is mode-aware (see ``reset_im_conversation``):

- ``flat`` mode (no Topic): delete the ``IMThreadLink``; the next message
  starts fresh.
- ``topic`` mode: repoint the link to a fresh ``Conversation`` under the
  same Topic so the old conversation stays as history.
"""

from __future__ import annotations

import re as _re
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ResetOutcome = Literal["none", "flat", "rotated"]

_RESET_RE = _re.compile(r"^\s*(?:/new|/reset|新对话)\s*$", _re.IGNORECASE)


def parse_reset_command(text: str) -> bool:
    """Return True if the message is a /new, /reset, or 新对话 command."""
    return bool(_RESET_RE.match(text or ""))


def format_reset_reply(outcome: ResetOutcome) -> str:
    """User-facing confirmation for a completed reset attempt."""
    if outcome == "none":
        return "ℹ️ 当前还没有进行中的会话，直接发送消息即可开始新对话。"
    return "✅ 新对话已开始。"


async def apply_reset_command(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    account_id: str,
    channel_id: str,
    scope_key: str,
) -> ResetOutcome:
    """Run ``reset_im_conversation`` and commit. Returns the outcome label."""
    from cubebox.im.conversation_resolver import reset_im_conversation

    async with session_maker() as session:
        outcome = await reset_im_conversation(
            session,
            account_id=account_id,
            channel_id=channel_id,
            scope_key=scope_key,
        )
        await session.commit()
    return outcome
