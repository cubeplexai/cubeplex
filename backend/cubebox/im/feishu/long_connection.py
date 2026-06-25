"""Feishu long-connection (WebSocket) inbound transport.

The SDK delivers events on its own worker thread. Crossing back to the
asyncio loop MUST use ``asyncio.run_coroutine_threadsafe`` against a loop
captured at startup; ``asyncio.get_event_loop()`` raises on Python 3.12+
when no loop is attached to the calling thread (hermes' equivalent at
``~/hermes-agent/gateway/platforms/feishu.py:2547`` uses the same pattern).

The SDK's marshal of ``P2ImMessageReceiveV1`` exposes ``data.event`` as the
inbound body and ``data.header`` as the envelope. To stay
parser-version-independent we reconstruct the webhook-style envelope
``{header: {event_id, event_type}, event: {...}}`` before calling
``FeishuConnector.parse_inbound``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from loguru import logger

from cubebox.api.routes.v1.im_ingress import _handle_card_action
from cubebox.im.feishu.connector import FeishuConnector

try:
    import lark_oapi as lark

    LARK_AVAILABLE = True
except ImportError:  # pragma: no cover — dep is required at runtime
    LARK_AVAILABLE = False
    lark = None


class _IngestCallable(Protocol):
    async def __call__(
        self,
        event: Any,
        *,
        account: Any,
        session_maker: Any,
    ) -> Any: ...


IngestCallable = Callable[..., Awaitable[Any]]


async def _lc_handle_card_action(event: Any, *, run_manager: Any, redis_key_prefix: str) -> Any:
    """Glue: convert the SDK's P2CardActionTrigger event into the dict
    envelope that ``_handle_card_action`` (the webhook ingress) accepts,
    invoke it, and return a ``P2CardActionTriggerResponse`` carrying any toast.
    """
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        CallBackToast,
        P2CardActionTriggerResponse,
    )

    data = getattr(event, "event", None)
    if data is None:
        envelope: dict[str, Any] = {
            "header": {"event_type": "card.action.trigger"},
            "event": {},
        }
    else:
        operator = getattr(data, "operator", None)
        action = getattr(data, "action", None)
        click_token = str(getattr(data, "token", "") or "")
        envelope = {
            "header": {
                "event_type": "card.action.trigger",
                # Keep at header.token for backward compatibility with any
                # caller that still reads from here; the canonical location
                # for the replay guard is event.token below.
                "token": click_token,
            },
            "event": {
                # The per-click interaction token. The HTTP webhook path puts
                # this at event.token (header.token in webhooks is Feishu's
                # static verification_token), so mirror that location here so
                # _handle_card_action's replay guard sees the click token on
                # both transport paths.
                "token": click_token,
                "operator": (
                    {"open_id": str(getattr(operator, "open_id", "") or "")}
                    if operator is not None
                    else {}
                ),
                "action": (
                    {"value": dict(getattr(action, "value", {}) or {})}
                    if action is not None
                    else {}
                ),
            },
        }

    _, toast = await _handle_card_action(
        envelope, run_manager=run_manager, redis_key_prefix=redis_key_prefix
    )
    response = P2CardActionTriggerResponse()
    if toast:
        cb_toast = CallBackToast()
        cb_toast.type = "info"
        cb_toast.content = toast
        response.toast = cb_toast
    return response


def build_event_handler(
    *,
    account: Any,
    bot_open_id: str,
    ingest: IngestCallable,
    session_maker: Any,
    loop: asyncio.AbstractEventLoop,
    run_manager: Any,
    redis_key_prefix: str,
    outbound_client: Any | None = None,
) -> Any:
    """Build a lark_oapi event dispatcher that routes events into ``ingest``.

    ``loop`` is the running asyncio loop captured at startup. The SDK
    callback fires on its own thread; we hop back via
    ``asyncio.run_coroutine_threadsafe``.

    ``outbound_client`` is an authenticated ``lark.Client`` used by the
    identity gate to call ``contact/v3/users`` and send rejection replies.
    Without it, ingest still works but every inbound is routed as the
    account's ``acting_user_id`` (no per-sender identity gate).
    """
    if not LARK_AVAILABLE:
        raise RuntimeError("lark_oapi not installed")
    assert lark is not None  # narrowed via LARK_AVAILABLE

    connector = FeishuConnector(bot_open_id=bot_open_id)
    gate_connector = (
        FeishuConnector(bot_open_id=bot_open_id, client=outbound_client)
        if outbound_client is not None
        else None
    )

    def _on_message(data: Any) -> None:
        # Reconstruct the webhook envelope so parse_inbound sees the same
        # shape on both transports. Header may live on the SDK object as a
        # ``header`` attribute (P2 events) — pull event_id / event_type from
        # there. The event body is ``data.event``.
        try:
            event_obj = getattr(data, "event", None)
            header_obj = getattr(data, "header", None)
            event_id = (getattr(header_obj, "event_id", "") or "") if header_obj else ""
            event_dict = json.loads(lark.JSON.marshal(event_obj)) if event_obj else {}
        except Exception:
            logger.exception("[Feishu LC] failed to marshal inbound event")
            return
        raw = {
            "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
            "event": event_dict,
        }

        async def _do_ingest() -> None:
            from cubebox.im.types import BindingMode, lookup_binding_mode

            channel_id = event_dict.get("message", {}).get("chat_id", "")
            bm: BindingMode = "isolated"
            if channel_id:
                bm = await lookup_binding_mode(session_maker, account.id, channel_id)
            event = connector.parse_inbound(raw, binding_mode=bm)
            if event is None:
                return
            event.account_external_id = account.external_account_id

            # Intercept /link or 绑定 commands before normal ingest — otherwise
            # the identity gate replies "I don't know who you are" instead of
            # issuing the link URL. The webhook path does the same in
            # cubebox/api/routes/v1/im_ingress.py.
            from cubebox.im.feishu.link_command import (
                handle_link_command,
                parse_link_command,
            )
            from cubebox.im.feishu.reset_command import (
                handle_reset_command,
                parse_reset_command,
            )

            link_email = parse_link_command(event.text)
            if link_email is not None:
                try:
                    await handle_link_command(
                        email=link_email,
                        event=event,
                        account=account,
                        connector=gate_connector,
                    )
                except Exception:
                    logger.exception(
                        "[Feishu LC] /link handler failed for {}",
                        event.platform_event_id,
                    )
                return

            if parse_reset_command(event.text):
                try:
                    await handle_reset_command(
                        event=event,
                        account=account,
                        session_maker=session_maker,
                        connector=gate_connector,
                    )
                except Exception:
                    logger.exception(
                        "[Feishu LC] /new handler failed for {}",
                        event.platform_event_id,
                    )
                return

            try:
                kwargs: dict[str, Any] = {}
                if gate_connector is not None:
                    kwargs["identity_resolver"] = gate_connector
                    kwargs["rejection_notifier"] = gate_connector
                res = await ingest(
                    event,
                    account=account,
                    session_maker=session_maker,
                    **kwargs,
                )
                logger.info(
                    "[Feishu LC] inbound {}: {}",
                    event.platform_event_id,
                    res.outcome,
                )
            except Exception:
                logger.exception(
                    "[Feishu LC] ingest failed for {}",
                    event.platform_event_id,
                )

        # Cross the thread boundary against the captured loop. Attach a
        # done_callback so failures BEFORE the coroutine starts running
        # (loop closed during shutdown, scheduling error) are logged loudly
        # — otherwise they're stored on the Future and silently swallowed.
        def _log_future(fut: Any) -> None:
            try:
                exc = fut.exception()
            except Exception:
                return
            if exc is not None:
                logger.warning(
                    "[Feishu LC] dispatch coroutine failed before completion: {}",
                    exc,
                )

        future = asyncio.run_coroutine_threadsafe(_do_ingest(), loop)
        future.add_done_callback(_log_future)

    def _on_card_action(data: Any) -> Any:
        """SDK-side sync handler: bridge to the captured asyncio loop and
        return a ``P2CardActionTriggerResponse`` synchronously."""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        future = asyncio.run_coroutine_threadsafe(
            _lc_handle_card_action(
                data, run_manager=run_manager, redis_key_prefix=redis_key_prefix
            ),
            loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            logger.warning("[Feishu LC] card.action handler raised: {}", exc)
            return P2CardActionTriggerResponse()

    # If the bot's Feishu app subscribes to additional IM events (reactions,
    # recall, read receipts, chat membership churn) the SDK raises
    # ``EventException: processor not found`` for every push we haven't
    # registered. Register silent no-ops for the common ones so the log
    # stays clean — promote any of these to a real handler when we add
    # the corresponding feature.
    def _noop(_: Any) -> None:
        return None

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card_action)
        .register_p2_im_message_reaction_created_v1(_noop)
        .register_p2_im_message_reaction_deleted_v1(_noop)
        .register_p2_im_message_recalled_v1(_noop)
        .register_p2_im_message_message_read_v1(_noop)
        .register_p2_im_chat_member_user_added_v1(_noop)
        .register_p2_im_chat_member_user_deleted_v1(_noop)
        .register_p2_im_chat_member_user_withdrawn_v1(_noop)
        .register_p2_im_chat_member_bot_added_v1(_noop)
        .register_p2_im_chat_member_bot_deleted_v1(_noop)
        .register_p2_im_chat_updated_v1(_noop)
        .register_p2_im_chat_disbanded_v1(_noop)
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_noop)
        .build()
    )


class FeishuLongConnection:
    """One lark_oapi WebSocket client per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        app_id: str,
        app_secret: str,
        bot_open_id: str,
        ingest: IngestCallable,
        session_maker: Any,
        run_manager: Any,
        redis_key_prefix: str,
        domain: str = "feishu",
    ) -> None:
        if not LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        self._account = account
        self._app_id = app_id
        self._app_secret = app_secret
        self._bot_open_id = bot_open_id
        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis_key_prefix = redis_key_prefix
        self._domain = domain
        self._ws_future: asyncio.Future[Any] | None = None
        self._client: Any = None
        self._thread_loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> None:
        assert lark is not None
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        domain = LARK_DOMAIN if self._domain == "lark" else FEISHU_DOMAIN
        # Capture loop NOW on the asyncio main thread; the handler closure
        # uses it via run_coroutine_threadsafe from the SDK worker.
        loop = asyncio.get_running_loop()
        # Outbound client for identity-gate calls (contact/v3/user.get) and
        # rejection replies. Same credentials as the WS client; the WS
        # ``Client`` doesn't expose the REST surface itself.
        outbound_client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        handler = build_event_handler(
            account=self._account,
            bot_open_id=self._bot_open_id,
            ingest=self._ingest,
            session_maker=self._session_maker,
            loop=loop,
            run_manager=self._run_manager,
            redis_key_prefix=self._redis_key_prefix,
            outbound_client=outbound_client,
        )
        self._client = lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
            domain=domain,
        )

        def _start_in_thread() -> None:
            # ``lark_oapi.ws.client`` captures a module-level ``loop`` at
            # import time via ``asyncio.get_event_loop()`` — under uvicorn
            # that grabs the main asyncio loop and then ``client.start()``
            # calls ``loop.run_until_complete()`` on it, raising
            # "This event loop is already running". Install a fresh loop on
            # this executor thread and replace the captured global so the
            # SDK's run_until_complete targets this thread instead.
            import lark_oapi.ws.client as _ws_client_mod

            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            _ws_client_mod.loop = new_loop
            self._thread_loop = new_loop
            try:
                self._client.start()
            finally:
                with suppress(Exception):
                    new_loop.close()
                self._thread_loop = None

        # ws.Client.start() is blocking — run it in a thread executor.
        self._ws_future = loop.run_in_executor(None, _start_in_thread)

    async def disconnect(self) -> None:
        # The lark SDK has no stop() method. client.start() blocks on
        # loop.run_until_complete(_select()) where _select is
        # `while True: sleep(3600)`. Break out by stopping the thread's
        # event loop, which unblocks run_until_complete and lets the
        # executor thread exit.
        tl = self._thread_loop
        if tl is not None:
            with suppress(Exception):
                # Close the WS connection first so _receive_message_loop exits.
                conn = getattr(self._client, "_conn", None)
                if conn is not None:
                    tl.call_soon_threadsafe(tl.create_task, conn.close())
            with suppress(Exception):
                tl.call_soon_threadsafe(tl.stop)
        if self._ws_future is not None:
            self._ws_future.cancel()

    def is_open(self) -> bool:
        """True iff the WebSocket task is still running.

        Reading this from the asyncio main thread is safe — the
        underlying Future is set/cleared by the SDK worker thread but
        our query is a single boolean read.
        """
        fut = self._ws_future
        return fut is not None and not fut.done()
