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
from typing import Any, Protocol

from loguru import logger

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


def build_event_handler(
    *,
    account: Any,
    bot_open_id: str,
    ingest: IngestCallable,
    session_maker: Any,
    loop: asyncio.AbstractEventLoop,
) -> Any:
    """Build a lark_oapi event dispatcher that routes events into ``ingest``.

    ``loop`` is the running asyncio loop captured at startup. The SDK
    callback fires on its own thread; we hop back via
    ``asyncio.run_coroutine_threadsafe``.
    """
    if not LARK_AVAILABLE:
        raise RuntimeError("lark_oapi not installed")
    assert lark is not None  # narrowed via LARK_AVAILABLE

    connector = FeishuConnector(bot_open_id=bot_open_id)

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
        event = connector.parse_inbound(raw)
        if event is None:
            return
        # The long-connection delivery is by definition bound to one account.
        event.account_external_id = account.external_account_id

        async def _do_ingest() -> None:
            try:
                res = await ingest(event, account=account, session_maker=session_maker)
                logger.info("[Feishu LC] inbound {}: {}", event.platform_event_id, res.outcome)
            except Exception:
                logger.exception("[Feishu LC] ingest failed for {}", event.platform_event_id)

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

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
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
        self._domain = domain
        self._ws_future: asyncio.Future[Any] | None = None
        self._client: Any = None

    async def connect(self) -> None:
        assert lark is not None
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        domain = LARK_DOMAIN if self._domain == "lark" else FEISHU_DOMAIN
        # Capture loop NOW on the asyncio main thread; the handler closure
        # uses it via run_coroutine_threadsafe from the SDK worker.
        loop = asyncio.get_running_loop()
        handler = build_event_handler(
            account=self._account,
            bot_open_id=self._bot_open_id,
            ingest=self._ingest,
            session_maker=self._session_maker,
            loop=loop,
        )
        self._client = lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
            domain=domain,
        )
        # ws.Client.start() is blocking — run it in a thread executor.
        self._ws_future = loop.run_in_executor(None, self._client.start)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                stop = getattr(self._client, "stop", None)
                if callable(stop):
                    stop()
            except Exception:
                logger.debug("[Feishu LC] stop() raised", exc_info=True)
        if self._ws_future is not None:
            self._ws_future.cancel()
