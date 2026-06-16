# backend/cubebox/im/discord/commands.py
"""Discord slash commands: /new and /reset."""

from __future__ import annotations

import discord
from discord.ext import commands
from loguru import logger


async def register_commands(bot: commands.Bot) -> None:
    """Register /new and /reset slash commands, then sync to all guilds."""

    @bot.tree.command(name="new", description="Start a new conversation")
    async def cmd_new(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    @bot.tree.command(name="reset", description="Reset the current conversation")
    async def cmd_reset(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    try:
        synced = await bot.tree.sync()
        logger.info("[Discord] Synced {} slash commands", len(synced))
    except Exception:
        logger.warning("[Discord] Failed to sync slash commands", exc_info=True)


async def _reset_conversation(interaction: discord.Interaction, bot: commands.Bot) -> None:
    """Delete the IMThreadLink for the current channel/scope so the next
    message starts a fresh conversation."""
    from cubebox.im.types import (
        DM_SCOPE_KEY,
        make_participant_scope,
        make_thread_participant_scope,
    )

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("无法确定频道。", ephemeral=True)
        return

    channel_type = getattr(channel, "type", None)
    channel_type_value = getattr(channel_type, "value", -1)
    is_dm = channel_type_value == 1
    is_thread = channel_type_value in (11, 12)

    sender_ref = str(interaction.user.id)
    channel_id = str(channel.id)
    if is_dm:
        scope_key = DM_SCOPE_KEY
    elif is_thread:
        scope_key = make_thread_participant_scope(sender_ref, channel_id)
    else:
        scope_key = make_participant_scope(sender_ref)

    from cubebox.models.im_connector import IMThreadLink

    session_maker = getattr(bot, "_cubebox_session_maker", None)
    account_id = getattr(bot, "_cubebox_account_id", None)
    if session_maker is None or account_id is None:
        await interaction.response.send_message("内部错误。", ephemeral=True)
        return

    async with session_maker() as session:
        from sqlmodel import select

        stmt = select(IMThreadLink).where(
            IMThreadLink.account_id == account_id,
            IMThreadLink.channel_id == channel_id,
            IMThreadLink.scope_key == scope_key,
        )
        link = (await session.execute(stmt)).scalar_one_or_none()
        if link is not None:
            await session.delete(link)
            await session.commit()

    await interaction.response.send_message(
        "✅ 新对话已开始。下一条消息将创建新的会话。", ephemeral=True
    )
