"""DingtalkPlatform — PlatformConnector implementation for DingTalk."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class DingtalkPlatform:
    """PlatformConnector for DingTalk (Stream mode only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.dingtalk.connector import DingtalkConnector

        connector = DingtalkConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubebox.im.dingtalk.connector import DingtalkConnector
        from cubebox.im.dingtalk.renderer import DingtalkOpDispatcher
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.types import RenderState

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})

        access_token = ""
        gw = gateways.get(account.id)
        if gw is not None:
            access_token = gw.access_token
            if not access_token:
                try:
                    access_token = await gw.refresh_access_token()
                except Exception:
                    logger.warning(
                        "[DingTalk] token refresh failed for {}",
                        account.id,
                    )

        is_dm = queue_item.scope_kind == "dm"
        connector = DingtalkConnector(
            bot_user_id=account.external_account_id,
            access_token=access_token,
            conversation_id=queue_item.channel_id,
            sender_staff_id=queue_item.sender_open_id,
            is_dm=is_dm,
        )

        cfg = account.config or {}
        state = RenderState(
            bot_name=cfg.get("bot_app_name") or "cubebox",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=1.0,
        )

        card_template_id = (gw.card_template_id if gw else "") or cfg.get("card_template_id", "")
        op_dispatcher = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            card_template_id=card_template_id,
            open_conversation_id=queue_item.channel_id,
        )

        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=op_dispatcher,
            responder_open_id=queue_item.sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubebox.im.dingtalk.gateway import DingtalkGateway
        from cubebox.im.inbound import ingest_inbound_event

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        app_key = str(secrets.get("app_key") or "")
        app_secret = str(secrets.get("app_secret") or "")
        if not app_key or not app_secret:
            logger.warning(
                "[DingTalk] skipping account {} — missing credentials",
                account.id,
            )
            return

        gw = DingtalkGateway(
            account=account,
            app_key=app_key,
            app_secret=app_secret,
            ingest=ingest_inbound_event,
            session_maker=session_maker,
            run_manager=run_manager,
            redis_key_prefix=redis_key_prefix,
        )
        await gw.refresh_access_token()
        await gw.start()
        gateways[account.id] = gw

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gw = gateways.pop(account.id, None)
        if gw is not None:
            await gw.stop()
