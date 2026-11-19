# backend/cubeplex/im/discord/commands.py
"""Discord slash commands: /new, /reset, /link, /stop, and /steer."""

from __future__ import annotations

import discord
from discord.ext import commands
from loguru import logger


async def register_commands(bot: commands.Bot) -> None:
    """Register /new, /reset, /link, /stop, and /steer slash commands."""

    @bot.tree.command(name="new", description="Start a new conversation")
    async def cmd_new(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    @bot.tree.command(name="reset", description="Reset the current conversation")
    async def cmd_reset(interaction: discord.Interaction) -> None:
        await _reset_conversation(interaction, bot)

    @bot.tree.command(name="link", description="Link your Discord account to CubePlex")
    @discord.app_commands.describe(email="Your CubePlex account email")
    async def cmd_link(interaction: discord.Interaction, email: str) -> None:
        await _initiate_link(interaction, bot, email=email)

    @bot.tree.command(name="stop", description="Stop the current reply")
    async def cmd_stop(interaction: discord.Interaction) -> None:
        await _handle_stop(interaction, bot)

    @bot.tree.command(name="steer", description="Steer the active reply")
    @discord.app_commands.describe(message="Follow-up instruction for the active reply")
    async def cmd_steer(interaction: discord.Interaction, message: str) -> None:
        await _handle_steer(interaction, bot, message=message)

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


async def _resolve_discord_scope(
    interaction: discord.Interaction,
    *,
    account_id: str,
    session_maker: object,
) -> tuple[str, str, str, str] | None:
    from cubeplex.im.types import (
        DM_SCOPE_KEY,
        lookup_binding_mode,
        make_channel_scope,
        make_participant_scope,
        make_thread_participant_scope,
        make_thread_scope,
    )

    channel = interaction.channel
    if channel is None:
        return None
    channel_type = getattr(channel, "type", None)
    channel_type_value = getattr(channel_type, "value", -1)
    is_dm = channel_type_value == 1
    is_thread = channel_type_value in (11, 12)
    sender_ref = str(interaction.user.id)
    channel_id = str(channel.id)
    if is_dm:
        return channel_id, sender_ref, DM_SCOPE_KEY, "dm"

    parent_id = getattr(channel, "parent_id", None) if is_thread else None
    lookup_channel_id = str(parent_id) if parent_id else channel_id
    binding_mode = await lookup_binding_mode(session_maker, account_id, lookup_channel_id)
    if binding_mode == "shared":
        scope_key = make_thread_scope(channel_id) if is_thread else make_channel_scope()
    elif is_thread:
        scope_key = make_thread_participant_scope(sender_ref, channel_id)
    else:
        scope_key = make_participant_scope(sender_ref)
    scope_kind = "thread" if is_thread else "channel"
    return channel_id, sender_ref, scope_key, scope_kind


async def _handle_stop(interaction: discord.Interaction, bot: commands.Bot) -> None:
    from cubeplex.im.control_commands import maybe_handle_control_command
    from cubeplex.im.types import InboundEvent
    from cubeplex.models.im_connector import IMConnectorAccount

    await interaction.response.defer(ephemeral=True)
    session_maker = getattr(bot, "_cubeplex_session_maker", None)
    account_id = getattr(bot, "_cubeplex_account_id", None)
    run_manager = getattr(bot, "_cubeplex_run_manager", None)
    if session_maker is None or account_id is None:
        await interaction.followup.send("内部错误。", ephemeral=True)
        return

    resolved = await _resolve_discord_scope(
        interaction, account_id=account_id, session_maker=session_maker
    )
    if resolved is None:
        await interaction.followup.send("无法确定频道。", ephemeral=True)
        return
    channel_id, sender_ref, scope_key, scope_kind = resolved

    async with session_maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
    if account is None:
        await interaction.followup.send("内部错误。", ephemeral=True)
        return

    event = InboundEvent(
        platform="discord",
        platform_event_id=f"slash:{interaction.id}",
        account_external_id=account.external_account_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        sender_ref=sender_ref,
        sender_open_id=sender_ref,
        text="/stop",
        reply_to_id=None,
        inbound_message_id="",
    )

    async def _reply(text: str) -> None:
        await interaction.followup.send(text, ephemeral=True)

    try:
        await maybe_handle_control_command(
            event,
            account=account,
            session_maker=session_maker,
            run_manager=run_manager,
            reply=_reply,
            require_linked_member=True,
        )
    except Exception:
        logger.opt(exception=True).warning("[Discord] /stop slash handler failed")
        await interaction.followup.send("停止当前回复失败。", ephemeral=True)


async def _handle_steer(
    interaction: discord.Interaction,
    bot: commands.Bot,
    *,
    message: str,
) -> None:
    from cubeplex.im.control_commands import (
        STEER_IDLE_FALLBACK_SLASH_REPLY,
        STEER_USAGE_REPLY,
        EphemeralRejectionNotifier,
        maybe_handle_control_command,
    )
    from cubeplex.im.identity import NullIdentityResolver
    from cubeplex.im.inbound import ingest_inbound_event
    from cubeplex.im.types import InboundEvent
    from cubeplex.models.im_connector import IMConnectorAccount

    await interaction.response.defer(ephemeral=True)
    content = (message or "").strip()
    if not content:
        await interaction.followup.send(STEER_USAGE_REPLY, ephemeral=True)
        return

    session_maker = getattr(bot, "_cubeplex_session_maker", None)
    account_id = getattr(bot, "_cubeplex_account_id", None)
    run_manager = getattr(bot, "_cubeplex_run_manager", None)
    if session_maker is None or account_id is None:
        await interaction.followup.send("内部错误。", ephemeral=True)
        return

    resolved = await _resolve_discord_scope(
        interaction, account_id=account_id, session_maker=session_maker
    )
    if resolved is None:
        await interaction.followup.send("无法确定频道。", ephemeral=True)
        return
    channel_id, sender_ref, scope_key, scope_kind = resolved

    async with session_maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
    if account is None:
        await interaction.followup.send("内部错误。", ephemeral=True)
        return

    event = InboundEvent(
        platform="discord",
        platform_event_id=f"slash:{interaction.id}",
        account_external_id=account.external_account_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        sender_ref=sender_ref,
        sender_open_id=sender_ref,
        text=f"/steer {content}",
        reply_to_id=None,
        inbound_message_id="",
    )

    async def _reply(text: str) -> None:
        await interaction.followup.send(text, ephemeral=True)

    try:
        handled = await maybe_handle_control_command(
            event,
            account=account,
            session_maker=session_maker,
            run_manager=run_manager,
            reply=_reply,
            require_linked_member=True,
        )
        if not handled:
            ingest_res = await ingest_inbound_event(
                event,
                account=account,
                session_maker=session_maker,
                identity_resolver=NullIdentityResolver(),
                rejection_notifier=EphemeralRejectionNotifier(_reply),
            )
            if ingest_res.outcome == "enqueued":
                await interaction.followup.send(STEER_IDLE_FALLBACK_SLASH_REPLY, ephemeral=True)
    except Exception:
        logger.opt(exception=True).warning("[Discord] /steer slash handler failed")
        await interaction.followup.send("处理 /steer 失败。", ephemeral=True)
