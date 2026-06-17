"""Slack Socket Mode gateway — one slack-bolt AsyncApp per IM account."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from cubebox.im.slack.connector import SlackConnector


class SlackGateway:
    """Manages one slack-bolt AsyncApp per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        bot_token: str,
        app_token: str,
        bot_user_id: str,
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
    ) -> None:
        self._account = account
        self._bot_token = bot_token
        self._app_token = app_token
        self._bot_user_id = bot_user_id
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._app: Any = None
        self._task: asyncio.Task[None] | None = None
        self._client: Any = None

    async def start(self) -> None:
        from slack_bolt.adapter.socket_mode.async_handler import (
            AsyncSocketModeHandler,
        )
        from slack_bolt.async_app import AsyncApp

        app = AsyncApp(token=self._bot_token)
        self._app = app
        self._client = app.client
        account = self._account
        session_maker = self._session_maker
        ingest = self._ingest
        bot_user_id = self._bot_user_id

        @app.event("app_mention")
        async def handle_mention(event: dict[str, Any], say: Any) -> None:
            await self._handle_inbound(event, bot_user_id, account, session_maker, ingest)

        @app.event("message")
        async def handle_message(event: dict[str, Any], say: Any) -> None:
            if event.get("channel_type") != "im":
                return
            await self._handle_inbound(event, bot_user_id, account, session_maker, ingest)

        @app.action(re.compile(r"^im:"))
        async def handle_action(ack: Any, action: dict[str, Any], body: dict[str, Any]) -> None:
            await ack()
            from cubebox.im.slack.interactions import handle_block_action

            await handle_block_action(
                action=action,
                body=body,
                run_manager=self._run_manager,
                redis_key_prefix=self._redis_key_prefix,
            )

        from cubebox.im.slack.commands import register_commands

        register_commands(
            app,
            account_id=account.id,
            workspace_id=account.workspace_id,
            session_maker=session_maker,
        )

        handler = AsyncSocketModeHandler(app, self._app_token)

        async def _run() -> None:
            try:
                await handler.start_async()  # type: ignore[no-untyped-call]
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Slack] Socket Mode handler crashed for {}", account.id)

        self._task = asyncio.create_task(_run(), name=f"slack-gateway:{account.id}")

        def _on_task_done(task: asyncio.Task[None]) -> None:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error(
                    "[Slack] gateway task crashed for {}: {}",
                    account.id,
                    exc,
                    exc_info=exc,
                )

        self._task.add_done_callback(_on_task_done)
        logger.info("[Slack] Gateway started for account {}", account.id)

    async def _handle_inbound(
        self,
        event: dict[str, Any],
        bot_user_id: str,
        account: Any,
        session_maker: Any,
        ingest: Any,
    ) -> None:
        connector = SlackConnector(bot_user_id=bot_user_id)
        parsed = connector.parse_inbound(event)
        if parsed is None:
            return
        parsed.account_external_id = account.external_account_id

        gate_connector = SlackConnector(
            bot_user_id=bot_user_id,
            client=self._client,
            channel_id=parsed.channel_id,
            thread_ts=parsed.reply_to_id,
        )
        try:
            result = await ingest(
                parsed,
                account=account,
                session_maker=session_maker,
                identity_resolver=gate_connector,
                rejection_notifier=gate_connector,
            )
            logger.info(
                "[Slack] inbound {}: {}",
                parsed.platform_event_id,
                result.outcome,
            )
        except Exception:
            logger.exception("[Slack] ingest failed for {}", parsed.platform_event_id)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("[Slack] Gateway stopped for account {}", self._account.id)

    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def client(self) -> Any:
        return self._client
