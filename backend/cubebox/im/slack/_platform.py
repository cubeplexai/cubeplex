"""SlackPlatform — PlatformConnector implementation for Slack."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class SlackPlatform:
    """PlatformConnector for Slack (Socket Mode only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.slack.connector import SlackConnector

        connector = SlackConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.slack.connector import SlackConnector
        from cubebox.im.slack.renderer import SlackOpDispatcher
        from cubebox.im.types import RenderState

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        load_secrets = kwargs.get("load_secrets")

        gw = gateways.get(account.id)
        client = gw.client if gw else None

        bot_user_id = ""
        if load_secrets is not None:
            secrets = await load_secrets(account)
            bot_user_id = secrets.get("bot_open_id", "")

        thread_ts = queue_item.reply_to_id

        sc = SlackConnector(
            bot_user_id=bot_user_id,
            client=client,
            channel_id=queue_item.channel_id,
            thread_ts=thread_ts,
        )
        cfg = account.config or {}
        state = RenderState(
            bot_name=cfg.get("bot_app_name") or "cubebox",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=1.0,
        )
        op_dispatcher = SlackOpDispatcher(connector=sc, state=state)
        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=sc,
            state=state,
            dispatcher=op_dispatcher,
            responder_open_id=queue_item.sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubebox.im.inbound import ingest_inbound_event
        from cubebox.im.slack.gateway import SlackGateway

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        bot_token = str(secrets.get("bot_token") or "")
        app_token = str(secrets.get("app_token") or "")
        bot_user_id = str(secrets.get("bot_open_id") or "")
        if not bot_token or not app_token:
            logger.warning("[Slack] skipping account {} — missing tokens", account.id)
            return

        gw = SlackGateway(
            account=account,
            bot_token=bot_token,
            app_token=app_token,
            bot_user_id=bot_user_id,
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
