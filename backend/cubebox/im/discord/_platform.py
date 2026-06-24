"""DiscordPlatform — PlatformConnector implementation for Discord."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class DiscordPlatform:
    """PlatformConnector for Discord (Gateway only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.discord.connector import DiscordConnector

        connector = DiscordConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubebox.im.discord.connector import DiscordConnector
        from cubebox.im.discord.renderer import DiscordOpDispatcher
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.types import RenderState

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        load_secrets = kwargs.get("load_secrets")

        gw = gateways.get(account.id)
        bot = gw.bot if gw else None

        # Read bot_user_id from credential (stable) instead of gateway
        # runtime state which may be None before on_ready fires.
        bot_user_id: int = 0
        if load_secrets is not None:
            secrets = await load_secrets(account)
            raw_id = secrets.get("bot_open_id", "")
            if raw_id:
                try:
                    bot_user_id = int(raw_id)
                except (ValueError, TypeError):
                    pass
        if not bot_user_id and gw is not None:
            bot_user_id = gw.bot_user_id or 0

        dc = DiscordConnector(
            bot_user_id=bot_user_id or 0,
            bot=bot,
            channel_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
        )
        cfg = account.config or {}
        state = RenderState(
            bot_name=cfg.get("bot_app_name") or "cubebox",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=1.2,
        )
        op_dispatcher = DiscordOpDispatcher(connector=dc, state=state)

        from cubebox.im.artifacts import IMArtifactDispatcher

        cfg_obj = kwargs.get("config")
        public_base = str(cfg_obj.get("api.public_url", "") or "") if cfg_obj is not None else ""
        artifact_disp = IMArtifactDispatcher(
            connector=dc,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
            public_base_url=public_base,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            conversation_id=queue_item.conversation_id,
            card_state=state.card_state,
            run_id=run_id,
            platform="discord",
            chat_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
            supports_inline_image=False,
        )

        shared_mode = False
        _sm = kwargs.get("session_maker")
        if _sm is not None:
            from cubebox.im.types import is_shared_mode_for_tailer

            shared_mode = await is_shared_mode_for_tailer(
                _sm,
                queue_item.account_id,
                queue_item.channel_id,
                queue_item.conversation_id,
            )

        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=dc,
            state=state,
            dispatcher=op_dispatcher,
            artifact_dispatcher=artifact_disp,
            responder_open_id=queue_item.sender_open_id,
            shared_mode=shared_mode,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubebox.im.discord.gateway import DiscordGateway
        from cubebox.im.inbound import ingest_inbound_event

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        bot_token = str(secrets.get("bot_token") or "")
        application_id = str(secrets.get("application_id") or "")
        if not bot_token:
            logger.warning("[Discord] skipping account {} — no bot_token", account.id)
            return

        gw = DiscordGateway(
            account=account,
            bot_token=bot_token,
            application_id=application_id,
            ingest=ingest_inbound_event,
            session_maker=session_maker,
            run_manager=run_manager,
            redis_key_prefix=redis_key_prefix,
        )
        await gw.start()
        gateways[account.id] = gw

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gw = gateways.pop(account.id, None)
        if gw is not None:
            await gw.stop()
