"""DingTalk Stream gateway — one long-connection per IM account."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import dingtalk_stream
import httpx
from loguru import logger

from cubeplex.im.dingtalk.connector import DingtalkConnector


class DingtalkGateway:
    """Manages one DingTalk Stream client per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        app_key: str,
        app_secret: str,
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
    ) -> None:
        self._account = account
        self._app_key = app_key
        self._app_secret = app_secret
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._client: dingtalk_stream.DingTalkStreamClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._access_token: str = ""
        self._stopping: bool = False
        self._shared_http = httpx.AsyncClient(timeout=10)

    async def start(self) -> None:
        credential = dingtalk_stream.Credential(self._app_key, self._app_secret)
        client = dingtalk_stream.DingTalkStreamClient(credential=credential)
        self._client = client

        account = self._account
        session_maker = self._session_maker
        ingest = self._ingest
        run_manager = self._run_manager

        async def on_message(raw: dict[str, Any]) -> None:
            await self._handle_inbound(raw, account, session_maker, ingest)

        async def on_card_action(raw: dict[str, Any]) -> None:
            from cubeplex.im.dingtalk.interactions import handle_card_action

            await handle_card_action(
                callback=raw,
                run_manager=run_manager,
            )

        client.register_callback_handler(
            dingtalk_stream.ChatbotMessage.TOPIC,
            _CallbackHandler(on_message),
        )
        client.register_callback_handler(
            "/v1.0/card/instances/callback",
            _CallbackHandler(on_card_action),
        )

        gw = self

        async def _run() -> None:
            backoff = 1.0
            while True:
                try:
                    await client.start()
                except asyncio.CancelledError:
                    return
                except Exception:
                    if gw._stopping:
                        return
                    logger.opt(exception=True).warning(
                        "[DingTalk] Stream disconnected for {}, reconnecting in {:.0f}s",
                        account.id,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)
                else:
                    if gw._stopping:
                        return
                    backoff = 1.0

        self._task = asyncio.create_task(_run(), name=f"dingtalk-gateway:{account.id}")

        async def _token_loop() -> None:
            while True:
                await asyncio.sleep(6000)
                try:
                    await self.refresh_access_token()
                    logger.debug("[DingTalk] token refreshed for {}", account.id)
                except Exception:
                    logger.opt(exception=True).warning(
                        "[DingTalk] token refresh failed for {}",
                        account.id,
                    )

        self._refresh_task = asyncio.create_task(
            _token_loop(), name=f"dingtalk-token-refresh:{account.id}"
        )
        logger.info("[DingTalk] Gateway started for account {}", account.id)

    async def _handle_inbound(
        self,
        raw: dict[str, Any],
        account: Any,
        session_maker: Any,
        ingest: Any,
    ) -> None:
        is_group = raw.get("conversationType") != "1"
        if is_group and not raw.get("isInAtList", False):
            return

        connector = DingtalkConnector(bot_user_id=self._app_key)
        if connector.is_link_command(raw):
            await self._handle_link_command(raw)
            return

        from cubeplex.im.reset_command import parse_reset_command
        from cubeplex.im.types import lookup_binding_mode

        channel_id = raw.get("conversationId", "")
        binding_mode = await lookup_binding_mode(session_maker, account.id, channel_id)
        parsed = connector.parse_inbound(raw, binding_mode=binding_mode)
        if parsed is None:
            return
        parsed.account_external_id = account.external_account_id

        if parse_reset_command(parsed.text):
            await self._handle_reset_command(parsed, account, session_maker)
            return

        is_dm = parsed.scope_kind == "dm"
        gate_connector = DingtalkConnector(
            bot_user_id=self._app_key,
            access_token=self._access_token,
            conversation_id=parsed.channel_id,
            sender_staff_id=parsed.sender_ref,
            is_dm=is_dm,
            http_client=self._shared_http,
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
                "[DingTalk] inbound {}: {}",
                parsed.platform_event_id,
                result.outcome,
            )
        except Exception:
            logger.exception(
                "[DingTalk] ingest failed for {}",
                parsed.platform_event_id,
            )

    def _reply_connector(
        self, *, conversation_id: str, sender_staff_id: str, is_dm: bool
    ) -> DingtalkConnector:
        return DingtalkConnector(
            bot_user_id=self._app_key,
            access_token=self._access_token,
            conversation_id=conversation_id,
            sender_staff_id=sender_staff_id,
            is_dm=is_dm,
            http_client=self._shared_http,
        )

    async def _handle_reset_command(
        self,
        event: Any,
        account: Any,
        session_maker: Any,
    ) -> None:
        """Handle /new or /reset by rotating the IM conversation binding."""
        from cubeplex.im.reset_command import apply_reset_command, format_reset_reply

        channel_id = event.channel_id or ""
        scope_key = event.scope_key or ""
        sender_staff_id = event.sender_ref or ""
        if not channel_id or not scope_key or not sender_staff_id:
            return

        try:
            outcome = await apply_reset_command(
                session_maker=session_maker,
                account_id=account.id,
                channel_id=channel_id,
                scope_key=scope_key,
            )
        except Exception:
            logger.exception("[DingTalk] /new handler failed for {}", event.platform_event_id)
            return

        is_dm = event.scope_kind == "dm"
        await self._reply_connector(
            conversation_id=channel_id,
            sender_staff_id=sender_staff_id,
            is_dm=is_dm,
        ).reply_markdown(
            title="New conversation",
            text=format_reset_reply(outcome),
            open_conversation_id=channel_id,
        )

    async def _handle_link_command(self, raw: dict[str, Any]) -> None:
        """Handle 'link alice@example.com' by sending an identity-link URL."""
        sender_staff_id = raw.get("senderStaffId", "")
        conversation_id = raw.get("conversationId", "")
        is_dm = raw.get("conversationType") == "1"
        if not sender_staff_id or not conversation_id:
            return

        reply = self._reply_connector(
            conversation_id=conversation_id,
            sender_staff_id=sender_staff_id,
            is_dm=is_dm,
        )

        connector = DingtalkConnector(bot_user_id=self._app_key)
        email = connector.parse_link_email(raw)
        if not email:
            await reply.reply_markdown(
                title="Link",
                text="Usage: `link alice@example.com`",
                open_conversation_id=conversation_id,
            )
            return

        try:
            from cubeplex.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token

            token = sign_link_token(
                im_user_id=sender_staff_id,
                email=email,
                account_id=self._account.id,
                workspace_id=self._account.workspace_id,
                platform="dingtalk",
                secret=get_jwt_secret(),
            )
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] sign_link_token failed")
            return

        base = get_frontend_base_url()
        url = f"{base}/im-link?token={token}"

        await reply.reply_markdown(
            title="Link your account",
            text=f"Click to bind your cubeplex account:\n\n[Link your account]({url})",
            open_conversation_id=conversation_id,
        )

    async def stop(self) -> None:
        self._stopping = True
        import logging as _logging

        for name in ("dingtalk_stream", "dingtalk_stream.client"):
            lg = _logging.getLogger(name)
            lg.setLevel(_logging.CRITICAL)
            lg.handlers.clear()
            lg.propagate = False
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client is not None:
            ws = getattr(self._client, "websocket", None)
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass
        await self._shared_http.aclose()
        logger.info("[DingTalk] Gateway stopped for account {}", self._account.id)

    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    async def refresh_access_token(self) -> str:
        """Refresh the access token for outbound API calls."""
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {"appKey": self._app_key, "appSecret": self._app_secret}
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, json=payload)
            data = resp.json()
            token: str = data.get("accessToken", "")
            if token:
                self._access_token = token
            else:
                logger.warning("[DingTalk] refresh returned empty token, keeping previous")
            return self._access_token

    @property
    def access_token(self) -> str:
        return self._access_token


class _CallbackHandler(dingtalk_stream.CallbackHandler):  # type: ignore[misc]
    """Routes SDK callbacks to our async handler."""

    def __init__(self, handler: Any) -> None:
        super().__init__()
        self._handler = handler

    async def process(
        self,
        callback: dingtalk_stream.CallbackMessage,
    ) -> tuple[int, str]:
        try:
            data = json.loads(callback.data) if isinstance(callback.data, str) else callback.data
            await self._handler(data)
        except Exception:
            logger.opt(exception=True).warning("[DingTalk] callback handler error")
        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
