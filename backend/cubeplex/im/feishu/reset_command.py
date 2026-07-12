"""Feishu /new and /reset command reply (parse/apply live in ``im.reset_command``).

Both the webhook ingress and the long-connection path must intercept
``/new`` / ``/reset`` (or the Chinese alias ``新对话``) BEFORE handing the
message to ``ingest_inbound_event`` — otherwise the message gets routed
to the agent as ordinary text.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.im.reset_command import (
    apply_reset_command,
    format_reset_reply,
    parse_reset_command,
)
from cubeplex.models.im_connector import IMConnectorAccount

# Re-export so existing Feishu call sites keep a stable import path.
__all__ = ["handle_reset_command", "parse_reset_command"]


async def handle_reset_command(
    *,
    event: Any,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    connector: Any,
) -> None:
    """Reset the current scope's conversation and reply with confirmation."""
    channel_id = event.channel_id or ""
    scope_key = event.scope_key or ""
    if not channel_id or not scope_key:
        if connector is not None:
            await connector.send_to_chat(channel_id, event.reply_to_id, "无法确定会话范围。")
        return

    outcome = await apply_reset_command(
        session_maker=session_maker,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
    )

    if connector is None:
        logger.warning("[Feishu] no connector to confirm /new reset")
        return

    await connector.send_to_chat(channel_id, event.reply_to_id, format_reset_reply(outcome))
