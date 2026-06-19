# backend/cubebox/im/discord/gateway.py
"""Discord Gateway lifecycle — one discord.py Bot per IM account.

The Bot runs in an asyncio.Task. ``start()`` decrypts the bot token,
creates the Bot, registers event handlers, and spawns the task.
``stop()`` calls ``bot.close()`` and cancels the task.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import discord
from discord.ext import commands
from loguru import logger

from cubebox.im.discord.connector import DiscordConnector


class DiscordGateway:
    """Manages one discord.py Bot per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        bot_token: str,
        application_id: str,
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
    ) -> None:
        self._account = account
        self._bot_token = bot_token
        self._application_id = application_id
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._bot: commands.Bot | None = None
        self._task: asyncio.Task[None] | None = None
        self._connector: DiscordConnector | None = None

    async def start(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.guild_reactions = True

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        bot = commands.Bot(
            command_prefix="!",
            intents=intents,
            application_id=int(self._application_id),
            proxy=proxy,
        )
        self._bot = bot
        account = self._account
        session_maker = self._session_maker
        ingest = self._ingest

        @bot.event
        async def on_ready() -> None:
            assert bot.user is not None
            logger.info(
                "[Discord] Bot ready: {} (id={})",
                bot.user.name,
                bot.user.id,
            )
            self._connector = DiscordConnector(bot_user_id=bot.user.id)
            # Store session_maker and account_id on bot for slash commands
            bot._cubebox_session_maker = session_maker  # type: ignore[attr-defined]
            bot._cubebox_account_id = account.id  # type: ignore[attr-defined]
            bot._cubebox_workspace_id = account.workspace_id  # type: ignore[attr-defined]
            from cubebox.im.discord.commands import register_commands

            await register_commands(bot)

        @bot.event
        async def on_message(message: discord.Message) -> None:
            if bot.user is None or self._connector is None:
                return

            from cubebox.im.types import lookup_binding_mode

            channel = message.channel
            channel_type_value = getattr(getattr(channel, "type", None), "value", -1)
            is_thread = channel_type_value in (11, 12)
            parent_id = getattr(channel, "parent_id", None) if is_thread else None
            lookup_channel_id = str(parent_id) if parent_id else str(channel.id)
            binding_mode = await lookup_binding_mode(session_maker, account.id, lookup_channel_id)
            event = self._connector.parse_inbound(message, binding_mode=binding_mode)
            if event is None:
                return
            event.account_external_id = account.external_account_id

            from cubebox.im.identity import NullIdentityResolver

            gate_connector = DiscordConnector(
                bot_user_id=bot.user.id,
                bot=bot,
                channel_id=event.channel_id,
                reply_to_id=event.reply_to_id,
            )
            try:
                result = await ingest(
                    event,
                    account=account,
                    session_maker=session_maker,
                    identity_resolver=NullIdentityResolver(),
                    rejection_notifier=gate_connector,
                )
                logger.info("[Discord] inbound {}: {}", event.platform_event_id, result.outcome)
            except Exception:
                logger.exception("[Discord] ingest failed for {}", event.platform_event_id)

        @bot.event
        async def on_interaction(interaction: discord.Interaction) -> None:
            if interaction.type == discord.InteractionType.component:
                from cubebox.im.discord.interactions import handle_component_interaction

                await handle_component_interaction(
                    interaction,
                    run_manager=self._run_manager,
                    redis_key_prefix=self._redis_key_prefix,
                )

        self._task = asyncio.create_task(
            bot.start(self._bot_token),
            name=f"discord-gateway:{account.id}",
        )

        def _on_task_done(task: asyncio.Task[None]) -> None:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error(
                    "[Discord] gateway task crashed for {}: {}",
                    account.id,
                    exc,
                    exc_info=exc,
                )

        self._task.add_done_callback(_on_task_done)

    async def stop(self) -> None:
        if self._bot is not None:
            try:
                await self._bot.close()
            except Exception:
                logger.debug("[Discord] bot.close() raised", exc_info=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def bot(self) -> commands.Bot | None:
        return self._bot

    @property
    def bot_user_id(self) -> int | None:
        if self._bot and self._bot.user:
            return self._bot.user.id
        return None
