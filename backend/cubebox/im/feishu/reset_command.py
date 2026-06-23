"""Feishu /new and /reset command parsing + reply.

Both the webhook ingress and the long-connection path must intercept
``/new`` / ``/reset`` (or the Chinese alias ``新对话``) BEFORE handing the
message to ``ingest_inbound_event`` — otherwise the message gets routed
to the agent as ordinary text, which politely acknowledges the request
without actually rotating the conversation.

The reset is implemented by deleting the ``IMThreadLink`` row keyed on
``(account_id, channel_id, scope_key)``. The next inbound message in the
same scope will hit ``conversation_resolver`` with no link and create a
fresh ``Conversation``. Mirrors the Discord ``/new`` handler at
``cubebox/im/discord/commands.py``.
"""

from __future__ import annotations

import re as _re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from cubebox.models.im_connector import IMConnectorAccount, IMThreadLink

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
    """Delete the IMThreadLink for the current scope and reply confirmation."""
    channel_id = event.channel_id or ""
    scope_key = event.scope_key or ""
    if not channel_id or not scope_key:
        if connector is not None:
            await connector.send_to_chat(
                channel_id, event.reply_to_id, "无法确定会话范围。"
            )
        return

    deleted = False
    async with session_maker() as session:
        stmt = select(IMThreadLink).where(
            IMThreadLink.account_id == account.id,
            IMThreadLink.channel_id == channel_id,
            IMThreadLink.scope_key == scope_key,
        )
        link = (await session.execute(stmt)).scalar_one_or_none()
        if link is not None:
            await session.delete(link)
            await session.commit()
            deleted = True

    if connector is None:
        logger.warning("[Feishu] no connector to confirm /new reset")
        return

    msg = (
        "✅ 新对话已开始。下一条消息将创建新的会话。"
        if deleted
        else "ℹ️ 当前还没有进行中的会话，直接发送消息即可开始新对话。"
    )
    await connector.send_to_chat(channel_id, event.reply_to_id, msg)
