"""FeishuPlatform — PlatformConnector implementation for Feishu."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class FeishuPlatform:
    """PlatformConnector for Feishu (long-connection + webhook)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.feishu.connector import FeishuConnector

        connector = FeishuConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubebox.im.artifacts import IMArtifactDispatcher
        from cubebox.im.feishu.connector import FeishuConnector
        from cubebox.im.feishu.op_dispatcher import FeishuOpDispatcher
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.runtime import _build_cardkit_client
        from cubebox.im.types import RenderState

        load_secrets = kwargs["load_secrets"]
        client_cache: dict[tuple[str, str], Any] = kwargs.get("client_cache", {})
        config = kwargs["config"]
        app = kwargs["app"]

        secrets = await load_secrets(account)
        account_key = (account.id, account.credential_id)

        # Build or reuse lark client
        if account_key in client_cache:
            client = client_cache[account_key]
        else:
            import lark_oapi as _lark
            from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

            domain = (
                LARK_DOMAIN if str(secrets.get("domain", "feishu")) == "lark" else FEISHU_DOMAIN
            )
            client = (
                _lark.Client.builder()
                .app_id(str(secrets["app_id"]))
                .app_secret(str(secrets["app_secret"]))
                .domain(domain)
                .log_level(_lark.LogLevel.WARNING)
                .build()
            )
            client_cache[account_key] = client

        connector = FeishuConnector(
            bot_open_id=str(secrets.get("bot_open_id") or "") or None,
            client=client,
            channel_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
        )
        state = RenderState(
            bot_name="cubebox",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
        )
        public_base = str(config.get("api.public_url", "") or "")
        artifact_disp = IMArtifactDispatcher(
            connector=connector,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
            public_base_url=public_base,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            conversation_id=queue_item.conversation_id,
            card_state=state.card_state,
            run_id=run_id,
            platform="feishu",
            chat_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
            supports_inline_image=True,
        )
        cardkit = _build_cardkit_client(client, secrets)
        op_dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)

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
            connector=connector,
            state=state,
            dispatcher=op_dispatcher,
            artifact_dispatcher=artifact_disp,
            responder_open_id=queue_item.sender_open_id,
            shared_mode=shared_mode,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubebox.im.feishu.long_connection import FeishuLongConnection
        from cubebox.im.inbound import ingest_inbound_event

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        long_connections: dict[str, Any] = kwargs.get("long_connections", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        bot_open_id = str(secrets.get("bot_open_id") or "")
        if not bot_open_id:
            logger.warning(
                "[IM] skipping long-connection for {} — bot_open_id not "
                "hydrated; re-run connect_feishu to fix",
                account.id,
            )
            return

        lc = FeishuLongConnection(
            account=account,
            app_id=str(secrets["app_id"]),
            app_secret=str(secrets["app_secret"]),
            bot_open_id=bot_open_id,
            ingest=ingest_inbound_event,
            session_maker=session_maker,
            run_manager=run_manager,
            redis_key_prefix=redis_key_prefix,
            domain=str(secrets.get("domain", "feishu")),
        )
        await lc.connect()
        long_connections[account.id] = lc

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        long_connections: dict[str, Any] = kwargs.get("long_connections", {})
        lc = long_connections.pop(account.id, None)
        if lc is not None:
            await lc.disconnect()
