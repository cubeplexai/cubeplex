"""WeCom AI Bot WebSocket transport."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger

WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
APP_CMD_SUBSCRIBE = "aibot_subscribe"
APP_CMD_CALLBACK = "aibot_msg_callback"
APP_CMD_LEGACY_CALLBACK = "aibot_callback"
APP_CMD_EVENT_CALLBACK = "aibot_event_callback"
APP_CMD_SEND = "aibot_send_msg"
APP_CMD_RESPONSE = "aibot_respond_msg"
APP_CMD_PING = "ping"

_CALLBACK_COMMANDS = frozenset({APP_CMD_CALLBACK, APP_CMD_LEGACY_CALLBACK})
_RECONNECT_BACKOFF = (2.0, 5.0, 10.0, 30.0, 60.0)
_CONNECT_TIMEOUT = 20.0
_ACK_TIMEOUT = 15.0
_HEARTBEAT_INTERVAL = 30.0

AsyncHook = Callable[[], Awaitable[None]]
InboundHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
SessionFactory = Callable[[], Any]


def _req_id(payload: dict[str, Any]) -> str:
    headers = payload.get("headers")
    if not isinstance(headers, dict):
        return ""
    return str(headers.get("req_id") or "").strip()


def _response_error(payload: dict[str, Any]) -> tuple[int, str]:
    source = payload
    if "errcode" not in source:
        body = payload.get("body")
        source = body if isinstance(body, dict) else {}
    try:
        code = int(source.get("errcode") or 0)
    except (TypeError, ValueError):
        code = -1
    return code, str(source.get("errmsg") or "")


class WecomAuthenticationError(ValueError):
    """The server explicitly rejected the Bot ID or Secret."""


class WecomUnavailableError(RuntimeError):
    """The credential probe could not reach or complete with WeCom."""


class WecomResponseError(RuntimeError):
    """WeCom rejected an authenticated proactive request."""


@dataclass(slots=True)
class _PassiveFrame:
    future: asyncio.Future[dict[str, Any]]
    final: bool


class WecomGateway:
    def __init__(
        self,
        *,
        bot_id: str,
        secret: str,
        session_factory: SessionFactory | None = None,
        inbound_handler: InboundHandler | None = None,
        connected: AsyncHook | None = None,
        disconnected: AsyncHook | None = None,
        terminal_disconnect: AsyncHook | None = None,
        websocket_url: str = WECOM_WS_URL,
        connect_timeout: float = _CONNECT_TIMEOUT,
        ack_timeout: float = _ACK_TIMEOUT,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL,
    ) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._session_factory = session_factory or aiohttp.ClientSession
        self._inbound_handler = inbound_handler
        self._connected_hook = connected
        self._disconnected_hook = disconnected
        self._terminal_hook = terminal_disconnect
        self._websocket_url = websocket_url
        self._connect_timeout = connect_timeout
        self._ack_timeout = ack_timeout
        self._heartbeat_interval = heartbeat_interval
        self._device_id = f"cubeplex-{uuid.uuid4().hex}"

        self._session: Any = None
        self._socket: Any = None
        self._running = False
        self._authenticated = False
        self._suppress_reconnect = False
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._inbound_tasks: set[asyncio.Task[None]] = set()
        self._requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._passive: dict[str, _PassiveFrame] = {}
        self._poisoned_passive_ids: set[str] = set()

    def configure_inbound(self, *, account: Any, session_maker: Any) -> None:
        """Bind database-backed callback routing after gateway construction."""

        async def handle(raw: dict[str, Any]) -> None:
            from cubeplex.im.wecom.ingress import handle_inbound_callback

            await handle_inbound_callback(
                raw,
                account=account,
                session_maker=session_maker,
                gateway=self,
            )

        self._inbound_handler = handle

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._suppress_reconnect = False
        try:
            await self._open_connection()
        except Exception:
            self._running = False
            await self._close_resources()
            raise
        self._reader_task = asyncio.create_task(self._reader_loop(), name="wecom-reader")
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="wecom-heartbeat",
        )
        try:
            await self._call_hook(self._connected_hook)
        except Exception:
            try:
                await self.stop()
            except Exception:
                logger.opt(exception=True).warning(
                    "[WeCom] cleanup failed after connection ownership rejection"
                )
            raise

    async def stop(self) -> None:
        self._running = False
        self._authenticated = False
        tasks: list[asyncio.Task[Any]] = []
        current = asyncio.current_task()
        for task in (self._reader_task, self._heartbeat_task, *self._inbound_tasks):
            if task is not None and task is not current and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inbound_tasks.clear()
        self._fail_waiters(RuntimeError("WeCom gateway stopped"))
        await self._close_resources()
        await self._call_hook(self._disconnected_hook)

    def is_open(self) -> bool:
        return bool(
            self._authenticated
            and self._socket is not None
            and not self._socket.closed
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def send_passive(
        self,
        req_id: str,
        body: dict[str, Any],
        *,
        final: bool,
        skip_if_pending: bool,
    ) -> dict[str, Any]:
        normalized = req_id.strip()
        if not normalized:
            raise ValueError("reply request id is required")
        if normalized in self._poisoned_passive_ids:
            return {"proactive_required": True, "reason": "poisoned_req_id"}
        prior = self._passive.get(normalized)
        if prior is not None and skip_if_pending:
            return {"skipped": True}
        if prior is not None and final:
            try:
                prior_response = await asyncio.wait_for(
                    asyncio.shield(prior.future),
                    timeout=self._ack_timeout,
                )
            except Exception:
                if not prior.future.done():
                    prior.future.cancel()
                self._passive.pop(normalized, None)
                self._poisoned_passive_ids.add(normalized)
                return {"proactive_required": True, "reason": "intermediate_ack_timeout"}
            prior_code, _prior_message = _response_error(prior_response)
            if prior_code:
                self._poisoned_passive_ids.add(normalized)
                return {
                    "proactive_required": True,
                    "reason": "intermediate_ack_error",
                    "errcode": prior_code,
                }

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        frame = _PassiveFrame(future=future, final=final)
        self._passive[normalized] = frame
        try:
            await self._send_json(
                {"cmd": APP_CMD_RESPONSE, "headers": {"req_id": normalized}, "body": body}
            )
        except Exception:
            if self._passive.get(normalized) is frame:
                self._passive.pop(normalized, None)
            future.cancel()
            raise
        if not final:
            return {"sent_nonblocking": True}
        try:
            return await asyncio.wait_for(future, timeout=self._ack_timeout)
        except TimeoutError:
            self._poisoned_passive_ids.add(normalized)
            return {
                "errcode": 0,
                "errmsg": "ack_timeout_assumed_delivered",
                "ack_pending": True,
            }
        finally:
            if self._passive.get(normalized) is frame:
                self._passive.pop(normalized, None)

    async def send_proactive(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not chat_id:
            raise ValueError("chat id is required")
        return await self._send_request(APP_CMD_SEND, {"chatid": chat_id, **body}, prefix="send")

    async def _open_connection(self) -> None:
        if self._session is None or getattr(self._session, "closed", False):
            self._session = self._session_factory()
        try:
            self._socket = await asyncio.wait_for(
                self._session.ws_connect(self._websocket_url),
                timeout=self._connect_timeout,
            )
            subscribe_id = f"subscribe-{uuid.uuid4().hex}"
            await self._send_json(
                {
                    "cmd": APP_CMD_SUBSCRIBE,
                    "headers": {"req_id": subscribe_id},
                    "body": {
                        "bot_id": self._bot_id,
                        "secret": self._secret,
                        "device_id": self._device_id,
                    },
                }
            )
            response = await self._receive_matching(subscribe_id)
            error_code, error_message = _response_error(response)
            if error_code:
                raise WecomAuthenticationError(
                    f"WeCom rejected the supplied credentials (errcode={error_code}): "
                    f"{error_message or 'authentication failed'}"
                )
        except WecomAuthenticationError:
            raise
        except Exception as exc:
            raise WecomUnavailableError("WeCom is unavailable; please try again") from exc
        self._authenticated = True

    async def _receive_matching(self, expected_req_id: str) -> dict[str, Any]:
        while True:
            message = await asyncio.wait_for(
                self._socket.receive(),
                timeout=self._connect_timeout,
            )
            if message.type != aiohttp.WSMsgType.TEXT:
                raise WecomUnavailableError("WeCom closed during authentication")
            payload = self._parse_payload(message.data)
            if payload is not None and _req_id(payload) == expected_req_id:
                return payload

    async def _reader_loop(self) -> None:
        backoff_index = 0
        while self._running:
            try:
                terminal = await self._read_until_closed()
                if terminal or not self._running:
                    return
                raise RuntimeError("WeCom socket closed")
            except asyncio.CancelledError:
                return
            except Exception:
                if not self._running or self._suppress_reconnect:
                    return
                await self._mark_disconnected()
                await self._close_socket()
                delay = _RECONNECT_BACKOFF[min(backoff_index, len(_RECONNECT_BACKOFF) - 1)]
                backoff_index += 1
                await asyncio.sleep(delay)
                if not self._running or self._suppress_reconnect:
                    return
                try:
                    await self._open_connection()
                except Exception:
                    logger.opt(exception=True).warning("[WeCom] reconnect failed")
                    continue
                backoff_index = 0
                try:
                    await self._call_hook(self._connected_hook)
                except Exception:
                    logger.opt(exception=True).warning(
                        "[WeCom] reconnect rejected by runtime ownership check"
                    )
                    self._running = False
                    self._suppress_reconnect = True
                    try:
                        await self._mark_disconnected()
                    finally:
                        await self._close_resources()
                    return

    async def _read_until_closed(self) -> bool:
        while self._running and self._socket is not None and not self._socket.closed:
            message = await self._socket.receive()
            if message.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError("WeCom socket closed")
            payload = self._parse_payload(message.data)
            if payload is None:
                continue
            if await self._dispatch_payload(payload):
                return True
        return False

    async def _dispatch_payload(self, payload: dict[str, Any]) -> bool:
        command = str(payload.get("cmd") or "")
        request_id = _req_id(payload)
        if request_id and command not in _CALLBACK_COMMANDS | {APP_CMD_EVENT_CALLBACK}:
            passive = self._passive.get(request_id)
            if passive is not None:
                if not passive.future.done():
                    passive.future.set_result(payload)
                self._passive.pop(request_id, None)
                return False
            pending = self._requests.get(request_id)
            if pending is not None:
                if not pending.done():
                    pending.set_result(payload)
                return False
        if command in _CALLBACK_COMMANDS and self._inbound_handler is not None:
            task = asyncio.create_task(
                self._inbound_handler(payload),
                name=f"wecom-inbound:{request_id or 'unknown'}",
            )
            self._inbound_tasks.add(task)
            task.add_done_callback(self._inbound_done)
            return False
        if command == APP_CMD_EVENT_CALLBACK:
            body = payload.get("body")
            event = body.get("event") if isinstance(body, dict) else None
            event_type = str(event.get("eventtype") or "") if isinstance(event, dict) else ""
            if event_type == "disconnected_event":
                self._suppress_reconnect = True
                self._running = False
                await self._mark_disconnected()
                tasks: list[asyncio.Task[None]] = []
                for background_task in (self._heartbeat_task, *self._inbound_tasks):
                    if background_task is not None and not background_task.done():
                        background_task.cancel()
                        tasks.append(background_task)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._inbound_tasks.clear()
                try:
                    await self._call_hook(self._terminal_hook)
                finally:
                    await self._close_resources()
                return True
        return False

    def _inbound_done(self, task: asyncio.Task[None]) -> None:
        self._inbound_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.opt(exception=True).warning("[WeCom] inbound callback failed")

    async def _send_request(
        self,
        command: str,
        body: dict[str, Any],
        *,
        prefix: str,
    ) -> dict[str, Any]:
        request_id = f"{prefix}-{uuid.uuid4().hex}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        try:
            await self._send_json({"cmd": command, "headers": {"req_id": request_id}, "body": body})
            response = await asyncio.wait_for(future, timeout=self._ack_timeout)
            error_code, error_message = _response_error(response)
            if error_code:
                raise WecomResponseError(
                    f"WeCom rejected the request (errcode={error_code}): "
                    f"{error_message or 'request failed'}"
                )
            return response
        finally:
            self._requests.pop(request_id, None)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._socket is None or self._socket.closed:
            raise RuntimeError("WeCom websocket is not connected")
        await self._socket.send_json(payload)

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._heartbeat_interval)
                if self._authenticated and self._socket is not None and not self._socket.closed:
                    await self._send_json(
                        {
                            "cmd": APP_CMD_PING,
                            "headers": {"req_id": f"ping-{uuid.uuid4().hex}"},
                            "body": {},
                        }
                    )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.opt(exception=True).warning("[WeCom] heartbeat failed")

    async def _mark_disconnected(self) -> None:
        was_authenticated = self._authenticated
        self._authenticated = False
        self._fail_waiters(RuntimeError("WeCom connection interrupted"))
        if was_authenticated:
            await self._call_hook(self._disconnected_hook)

    def _fail_waiters(self, error: Exception) -> None:
        for future in list(self._requests.values()):
            if not future.done():
                future.set_exception(error)
        self._requests.clear()
        for frame in list(self._passive.values()):
            if not frame.future.done():
                frame.future.set_exception(error)
        self._passive.clear()

    async def _close_socket(self) -> None:
        if self._socket is not None and not self._socket.closed:
            with suppress(Exception):
                await self._socket.close()
        self._socket = None

    async def _close_resources(self) -> None:
        await self._close_socket()
        if self._session is not None and not getattr(self._session, "closed", False):
            with suppress(Exception):
                await self._session.close()
        self._session = None

    @staticmethod
    def _parse_payload(raw: Any) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    async def _call_hook(hook: AsyncHook | None) -> None:
        if hook is not None:
            await hook()


async def probe_wecom_credentials(
    bot_id: str,
    secret: str,
    *,
    session_factory: SessionFactory | None = None,
    timeout: float = _CONNECT_TIMEOUT,
) -> None:
    gateway = WecomGateway(
        bot_id=bot_id,
        secret=secret,
        session_factory=session_factory,
        connect_timeout=timeout,
        heartbeat_interval=3600,
    )
    try:
        await gateway.start()
    finally:
        await gateway.stop()
