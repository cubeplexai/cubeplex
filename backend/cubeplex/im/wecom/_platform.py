"""WeCom platform adapter for the shared IM runtime."""

from __future__ import annotations

import asyncio
from typing import Any


class WecomPlatform:
    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubeplex.im.wecom.connector import WecomConnector

        return WecomConnector().parse_inbound(raw)

    async def build_tailer(
        self,
        *,
        run_id: str,
        queue_item: Any,
        account: Any,
        **kwargs: Any,
    ) -> Any:
        from cubeplex.im.artifacts import IMArtifactDispatcher
        from cubeplex.im.outbound import OutboundRunTailer
        from cubeplex.im.types import RenderState, is_shared_mode_for_tailer
        from cubeplex.im.wecom.connector import WecomConnector
        from cubeplex.im.wecom.renderer import WecomOpDispatcher

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gateway = gateways.get(account.id)
        if gateway is None:
            raise RuntimeError(f"WeCom gateway is unavailable for account {account.id}")
        connector = WecomConnector(
            bot_id=account.external_account_id,
            gateway=gateway,
            chat_id=queue_item.channel_id,
            reply_req_id=queue_item.reply_to_id,
        )
        state = RenderState(
            bot_name=(account.config or {}).get("bot_app_name") or "CubePlex",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=0.5,
        )
        dispatcher = WecomOpDispatcher(connector=connector, state=state)
        config = kwargs.get("config")
        public_base = str(config.get("api.public_url", "") or "") if config else ""
        artifacts = IMArtifactDispatcher(
            connector=connector,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
            public_base_url=public_base,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            conversation_id=queue_item.conversation_id,
            card_state=state.card_state,
            run_id=run_id,
            platform="wecom",
            chat_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
            supports_inline_image=False,
        )
        shared_mode = await is_shared_mode_for_tailer(
            kwargs["session_maker"],
            queue_item.account_id,
            queue_item.channel_id,
            queue_item.conversation_id,
        )
        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=dispatcher,
            artifact_dispatcher=artifacts,
            responder_open_id=queue_item.sender_open_id,
            shared_mode=shared_mode,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubeplex.im.wecom.gateway import WecomGateway

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        bot_id = str(secrets.get("bot_id") or account.external_account_id)
        secret = str(secrets.get("secret") or "")
        if not bot_id or not secret:
            raise ValueError("WeCom Bot ID and Secret are required")
        gateway = WecomGateway(
            bot_id=bot_id,
            secret=secret,
            connected=kwargs.get("connection_opened"),
            disconnected=kwargs.get("connection_closed"),
            terminal_disconnect=kwargs.get("terminal_disconnect"),
        )
        gateway.configure_inbound(account=account, session_maker=kwargs["session_maker"])
        gateways[account.id] = gateway
        try:
            await gateway.start()
        except Exception:
            gateways.pop(account.id, None)
            await gateway.stop()
            raise

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gateway = gateways.pop(account.id, None)
        if gateway is not None:
            await gateway.stop()
