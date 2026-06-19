"""Teams platform connector — webhook delivery mode.

Implements the 4-method ``PlatformConnector`` protocol.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class TeamsPlatform:
    """PlatformConnector implementation for Microsoft Teams."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.teams.connector import TeamsConnector

        connector = TeamsConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self,
        *,
        run_id: str,
        queue_item: Any,
        account: Any,
        **kwargs: Any,
    ) -> Any:
        from cubebox.im.outbound import OutboundRunTailer
        from cubebox.im.teams.app_manager import get_entry_by_bot_id
        from cubebox.im.teams.connector import TeamsConnector
        from cubebox.im.teams.graph import TeamsGraphClient
        from cubebox.im.teams.renderer import TeamsOpDispatcher
        from cubebox.im.types import RenderState

        app = kwargs["app"]
        redis = app.state.redis
        key_prefix = app.state.redis_key_prefix

        load_secrets = kwargs["load_secrets"]
        secrets = await load_secrets(account)

        bot_id = str(secrets.get("app_id") or account.external_account_id)
        entry = get_entry_by_bot_id(bot_id)
        sdk_app = entry.app if entry else None

        graph_client = TeamsGraphClient(
            app_id=str(secrets.get("app_id", "")),
            app_secret=str(secrets.get("app_secret", "")),
            tenant_id=str(secrets.get("tenant_id", "")),
        )

        channel_id = queue_item.channel_id
        reply_to_id = queue_item.reply_to_id

        connector = TeamsConnector(
            bot_id=bot_id,
            app=sdk_app,
            channel_id=channel_id,
            reply_to_id=reply_to_id,
            graph_client=graph_client,
        )

        bot_name = (account.config or {}).get("bot_app_name") or "cubebox"
        state = RenderState(
            bot_name=bot_name,
            run_id=run_id,
            stream_interval=1.5,
            reply_to_id=reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
        )

        dispatcher = TeamsOpDispatcher(connector=connector, state=state)

        sender_open_id = queue_item.sender_open_id or queue_item.sender_im_user_id
        tailer = OutboundRunTailer(
            redis=redis,
            key_prefix=key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=dispatcher,
            responder_open_id=sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        """Initialize the App instance so the webhook ingress can dispatch."""
        from cubebox.im.teams.app_manager import init_app

        secrets: dict[str, Any] = kwargs["secrets"]
        bot_id = str(secrets.get("app_id") or account.external_account_id)

        try:
            await init_app(
                account_id=account.id,
                bot_id=bot_id,
                secrets=secrets,
            )
        except Exception:
            logger.exception(
                "[Teams] app init failed for account {} bot_id={}",
                account.id,
                bot_id,
            )

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        """Remove the App instance from cache."""
        from cubebox.im.teams.app_manager import remove_app

        load_secrets = kwargs.get("load_secrets")
        if load_secrets:
            try:
                secrets = await load_secrets(account)
                bot_id = str(secrets.get("app_id") or account.external_account_id)
            except Exception:
                bot_id = account.external_account_id
        else:
            bot_id = account.external_account_id
        remove_app(bot_id)
