# backend/cubeplex/im/discord/commands.py
"""Discord slash commands: /new, /reset, and /link."""

from __future__ import annotations

import discord
from discord.ext import commands
from loguru import logger


async def register_commands(bot: commands.Bot) -> None:
    """Register /new, /reset, and /link slash commands, then sync to all guilds."""

    @bot.tree.command(name="new", description="Start a new conversation")
    async def cmd_new(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    @bot.tree.command(name="reset", description="Reset the current conversation")
    async def cmd_reset(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    @bot.tree.command(name="link", description="Link your Discord account to cubeplex")
    @discord.app_commands.describe(email="Your cubeplex account email")
    async def cmd_link(interaction: discord.Interaction, email: str) -> None:
        await _initiate_link(interaction, bot, email=email)

    try:
        synced = await bot.tree.sync()
        logger.info("[Discord] Synced {} slash commands", len(synced))
    except Exception:
        logger.opt(exception=True).warning("[Discord] Failed to sync slash commands")


async def _reset_conversation(interaction: discord.Interaction, bot: commands.Bot) -> None:
    """Delete/rotate the IMThreadLink for the current channel/scope so the
    next message starts a fresh conversation."""
    from cubeplex.im.reset_command import apply_reset_command, format_reset_reply
    from cubeplex.im.types import (
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

    session_maker = getattr(bot, "_cubeplex_session_maker", None)
    account_id = getattr(bot, "_cubeplex_account_id", None)
    if session_maker is None or account_id is None:
        await interaction.response.send_message("内部错误。", ephemeral=True)
        return

    # Route through the mode-aware reset so topic-mode accounts rotate the
    # conversation under the same durable Topic instead of dropping the anchor
    # (which would spawn a second Topic on the next message).
    outcome = await apply_reset_command(
        session_maker=session_maker,
        account_id=account_id,
        channel_id=channel_id,
        scope_key=scope_key,
    )
    await interaction.response.send_message(format_reset_reply(outcome), ephemeral=True)


async def _initiate_link(
    interaction: discord.Interaction,
    bot: commands.Bot,
    *,
    email: str,
) -> None:
    """Generate a link token and reply with the confirmation URL."""
    # Defer immediately — lazy imports below can exceed Discord's 3s deadline.
    await interaction.response.defer(ephemeral=True)

    account_id = getattr(bot, "_cubeplex_account_id", None)
    workspace_id = getattr(bot, "_cubeplex_workspace_id", None)
    if not account_id or not workspace_id:
        await interaction.followup.send("内部错误。", ephemeral=True)
        return

    from cubeplex.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token

    sender_ref = str(interaction.user.id)
    try:
        token = sign_link_token(
            im_user_id=sender_ref,
            email=email,
            account_id=account_id,
            workspace_id=workspace_id,
            platform="discord",
            secret=get_jwt_secret(),
        )
    except Exception:
        logger.opt(exception=True).warning("[Discord] sign_link_token failed")
        await interaction.followup.send("生成绑定链接失败。", ephemeral=True)
        return

    base = get_frontend_base_url()
    url = f"{base}/im-link?token={token}"
    await interaction.followup.send(
        f"点击链接完成绑定：\n{url}",
        ephemeral=True,
    )
