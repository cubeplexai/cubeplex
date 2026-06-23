"""Feishu /new and /reset command parsing + reply.

Both the webhook ingress and the long-connection path must intercept
``/new`` / ``/reset`` (or the Chinese alias ``新对话``) BEFORE handing the
message to ``ingest_inbound_event`` — otherwise the message gets routed
to the agent as ordinary text, which politely acknowledges the request
without actually rotating the conversation.

The reset is mode-aware (see ``reset_im_conversation``):

- ``flat`` mode (no Topic): delete the ``IMThreadLink``; the next message
  hits ``conversation_resolver`` with no link and starts fresh. Mirrors the
  Discord ``/new`` handler at ``cubebox/im/discord/commands.py``.
- ``topic`` mode: repoint the link to a fresh ``Conversation`` under the
  same Topic so the old conversation stays as history under the Topic.
"""

from __future__ import annotations

import re as _re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.models.im_connector import IMConnectorAccount

_RESET_RE = _re.compile(r"^\s*(?:/new|/reset|新对话)\s*$", _re.IGNORECASE)


def parse_reset_command(text: str) -> bool:
    """Return True if the message is a /new, /reset, or 新对话 command."""
    return bool(_RESET_RE.match(text or ""))


async def handle_reset_command(
    *,
    event: Any,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    connector: Any,
) -> None:
    """Reset the current scope's conversation and reply with confirmation."""
    from cubebox.im.conversation_resolver import reset_im_conversation

    channel_id = event.channel_id or ""
    scope_key = event.scope_key or ""
    if not channel_id or not scope_key:
        if connector is not None:
            await connector.send_to_chat(
                channel_id, event.reply_to_id, "无法确定会话范围。"
            )
        return

    async with session_maker() as session:
        outcome = await reset_im_conversation(
            session,
            account_id=account.id,
            channel_id=channel_id,
            scope_key=scope_key,
        )
        await session.commit()

    if connector is None:
        logger.warning("[Feishu] no connector to confirm /new reset")
        return

    msg = (
        "ℹ️ 当前还没有进行中的会话，直接发送消息即可开始新对话。"
        if outcome == "none"
        else "✅ 新对话已开始。"
    )
    await connector.send_to_chat(channel_id, event.reply_to_id, msg)
