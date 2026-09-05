from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

import cubeplex.im.wecom.gateway as gateway_module
from cubeplex.im.wecom.gateway import (
    APP_CMD_CALLBACK,
    APP_CMD_EVENT_CALLBACK,
    APP_CMD_PING,
    APP_CMD_RESPONSE,
    APP_CMD_SEND,
    APP_CMD_SUBSCRIBE,
    WECOM_WS_URL,
    WecomAuthenticationError,
    WecomGateway,
    WecomResponseError,
    WecomUnavailableError,
    probe_wecom_credentials,
)


class _Message:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = aiohttp.WSMsgType.TEXT
        self.data = json.dumps(payload)


class _ClosedMessage:
    type = aiohttp.WSMsgType.CLOSED
    data = ""


class _FakeSocket:
    def __init__(
        self,
        on_send: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.closed = False
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[_Message | _ClosedMessage] = asyncio.Queue()
        self.on_send = on_send

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if self.on_send is not None:
            await self.on_send(payload)

    async def receive(self) -> _Message | _ClosedMessage:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(_Message(payload))

    async def push_closed(self) -> None:
        await self.incoming.put(_ClosedMessage())


class _FakeSession:
    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket
        self.closed = False
        self.urls: list[str] = []

    async def ws_connect(self, url: str, **kwargs: Any) -> _FakeSocket:
        del kwargs
        self.urls.append(url)
        return self.socket

    async def close(self) -> None:
        self.closed = True


class _CyclingSession:
    def __init__(self, sockets: list[_FakeSocket]) -> None:
        self.sockets = iter(sockets)
        self.closed = False
        self.urls: list[str] = []

    async def ws_connect(self, url: str, **kwargs: Any) -> _FakeSocket:
        del kwargs
        self.urls.append(url)
        return next(self.sockets)

    async def close(self) -> None:
        self.closed = True


def _success_session() -> tuple[_FakeSession, _FakeSocket]:
    socket = _FakeSocket()

    async def respond(payload: dict[str, Any]) -> None:
        if payload["cmd"] == APP_CMD_SUBSCRIBE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )

    socket.on_send = respond
    return _FakeSession(socket), socket


def _success_socket() -> _FakeSocket:
    socket = _FakeSocket()

    async def respond(payload: dict[str, Any]) -> None:
        if payload["cmd"] == APP_CMD_SUBSCRIBE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )

    socket.on_send = respond
    return socket


@pytest.mark.asyncio
async def test_start_sends_subscribe_and_stop_closes_resources() -> None:
    session, socket = _success_session()
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=3600,
    )

    await gateway.start()

    assert gateway.is_open()
    assert session.urls == [WECOM_WS_URL]
    subscribe = socket.sent[0]
    assert subscribe["cmd"] == APP_CMD_SUBSCRIBE
    assert subscribe["headers"]["req_id"].startswith("subscribe-")
    assert subscribe["body"]["bot_id"] == "bot-1"
    assert subscribe["body"]["secret"] == "secret-1"
    assert subscribe["body"]["device_id"]

    await gateway.stop()

    assert socket.closed
    assert session.closed
    assert not gateway.is_open()


@pytest.mark.asyncio
async def test_start_closes_resources_when_runtime_rejects_ownership() -> None:
    session, socket = _success_session()
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        connected=AsyncMock(side_effect=RuntimeError("lease ownership lost")),
        heartbeat_interval=3600,
    )

    with pytest.raises(RuntimeError, match="lease ownership lost"):
        await gateway.start()

    assert socket.closed
    assert session.closed
    assert not gateway.is_open()


@pytest.mark.asyncio
async def test_reader_dispatches_handler_without_blocking_its_passive_ack() -> None:
    session, socket = _success_session()
    handled = asyncio.Event()
    gateway: WecomGateway

    async def on_send(payload: dict[str, Any]) -> None:
        if payload["cmd"] == APP_CMD_SUBSCRIBE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )
        elif payload["cmd"] == APP_CMD_RESPONSE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )

    socket.on_send = on_send

    async def handle(frame: dict[str, Any]) -> None:
        await gateway.send_passive(
            frame["headers"]["req_id"],
            {"msgtype": "markdown", "markdown": {"content": "linked"}},
            final=True,
            skip_if_pending=False,
        )
        handled.set()

    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        inbound_handler=handle,
        heartbeat_interval=3600,
    )
    await gateway.start()
    await socket.push(
        {
            "cmd": APP_CMD_CALLBACK,
            "headers": {"req_id": "callback-1"},
            "body": {"msgid": "msg-1"},
        }
    )

    await asyncio.wait_for(handled.wait(), timeout=0.5)
    await gateway.stop()


@pytest.mark.asyncio
async def test_passive_intermediate_timeout_poison_switches_final_to_proactive() -> None:
    session, _socket = _success_session()
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=3600,
        ack_timeout=0.01,
    )
    await gateway.start()

    intermediate = await gateway.send_passive(
        "callback-1",
        {"msgtype": "stream", "stream": {"id": "s1", "finish": False}},
        final=False,
        skip_if_pending=True,
    )
    final = await gateway.send_passive(
        "callback-1",
        {"msgtype": "stream", "stream": {"id": "s1", "finish": True}},
        final=True,
        skip_if_pending=False,
    )

    assert intermediate["sent_nonblocking"] is True
    assert final["proactive_required"] is True
    response_frames = [item for item in _socket.sent if item["cmd"] == APP_CMD_RESPONSE]
    assert len(response_frames) == 1
    await gateway.stop()


@pytest.mark.asyncio
async def test_passive_intermediate_error_switches_final_to_proactive() -> None:
    session, socket = _success_session()

    async def on_send(payload: dict[str, Any]) -> None:
        if payload["cmd"] == APP_CMD_SUBSCRIBE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )
        elif payload["cmd"] == APP_CMD_RESPONSE:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 846604,
                    "errmsg": "callback expired",
                }
            )

    socket.on_send = on_send
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=3600,
    )
    await gateway.start()

    await gateway.send_passive(
        "callback-expired",
        {"msgtype": "stream", "stream": {"id": "s1", "finish": False}},
        final=False,
        skip_if_pending=True,
    )
    final = await gateway.send_passive(
        "callback-expired",
        {"msgtype": "stream", "stream": {"id": "s1", "finish": True}},
        final=True,
        skip_if_pending=False,
    )

    assert final["proactive_required"] is True
    response_frames = [item for item in socket.sent if item["cmd"] == APP_CMD_RESPONSE]
    assert len(response_frames) == 1
    await gateway.stop()


@pytest.mark.asyncio
async def test_proactive_request_uses_unique_correlation_and_chat_id() -> None:
    session, socket = _success_session()

    async def on_send(payload: dict[str, Any]) -> None:
        if payload["cmd"] in {APP_CMD_SUBSCRIBE, APP_CMD_SEND}:
            await socket.push(
                {
                    "headers": {"req_id": payload["headers"]["req_id"]},
                    "errcode": 0,
                    "errmsg": "ok",
                }
            )

    socket.on_send = on_send
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=3600,
    )
    await gateway.start()

    response = await gateway.send_proactive(
        "chat-1",
        {"msgtype": "markdown", "markdown": {"content": "hello"}},
    )

    frame = [item for item in socket.sent if item["cmd"] == APP_CMD_SEND][0]
    assert frame["headers"]["req_id"].startswith("send-")
    assert frame["body"] == {
        "chatid": "chat-1",
        "msgtype": "markdown",
        "markdown": {"content": "hello"},
    }
    assert response["errcode"] == 0
    await gateway.stop()


@pytest.mark.asyncio
async def test_proactive_request_rejects_nonzero_ack() -> None:
    session, socket = _success_session()

    async def on_send(payload: dict[str, Any]) -> None:
        code = 0 if payload["cmd"] == APP_CMD_SUBSCRIBE else 40003
        await socket.push(
            {
                "headers": {"req_id": payload["headers"]["req_id"]},
                "errcode": code,
                "errmsg": "invalid chat" if code else "ok",
            }
        )

    socket.on_send = on_send
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=3600,
    )
    await gateway.start()

    with pytest.raises(WecomResponseError, match="errcode=40003"):
        await gateway.send_proactive(
            "missing-chat",
            {"msgtype": "markdown", "markdown": {"content": "hello"}},
        )
    await gateway.stop()


@pytest.mark.asyncio
async def test_terminal_disconnect_calls_runtime_hook_and_stops_reconnect() -> None:
    session, socket = _success_session()
    terminal_seen = asyncio.Event()

    async def on_terminal() -> None:
        terminal_seen.set()

    terminal = AsyncMock(side_effect=on_terminal)
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        terminal_disconnect=terminal,
        heartbeat_interval=3600,
    )
    await gateway.start()

    await socket.push(
        {
            "cmd": APP_CMD_EVENT_CALLBACK,
            "headers": {"req_id": "event-1"},
            "body": {"event": {"eventtype": "disconnected_event"}},
        }
    )
    await asyncio.wait_for(terminal_seen.wait(), timeout=0.5)

    terminal.assert_awaited_once()
    assert not gateway.is_open()
    assert socket.closed
    assert session.closed
    await gateway.stop()


@pytest.mark.asyncio
async def test_ordinary_disconnect_reconnects_and_updates_runtime_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module, "_RECONNECT_BACKOFF", (0.0,))
    first_socket = _success_socket()
    second_socket = _success_socket()
    session = _CyclingSession([first_socket, second_socket])
    reconnected = asyncio.Event()

    async def on_connected() -> None:
        if connected.await_count == 2:
            reconnected.set()

    connected = AsyncMock(side_effect=on_connected)
    disconnected = AsyncMock()
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        connected=connected,
        disconnected=disconnected,
        heartbeat_interval=3600,
    )
    await gateway.start()

    await first_socket.push_closed()
    await asyncio.wait_for(reconnected.wait(), timeout=0.5)

    assert first_socket.closed
    assert gateway.is_open()
    assert len(session.urls) == 2
    connected.assert_awaited()
    assert connected.await_count == 2
    disconnected.assert_awaited_once()
    await gateway.stop()


@pytest.mark.asyncio
async def test_heartbeat_frame_shape() -> None:
    session, socket = _success_session()
    gateway = WecomGateway(
        bot_id="bot-1",
        secret="secret-1",
        session_factory=lambda: session,
        heartbeat_interval=0.01,
    )
    await gateway.start()

    async with asyncio.timeout(0.5):
        while not any(frame["cmd"] == APP_CMD_PING for frame in socket.sent):
            await asyncio.sleep(0)

    ping = [frame for frame in socket.sent if frame["cmd"] == APP_CMD_PING][0]
    assert ping["body"] == {}
    assert ping["headers"]["req_id"].startswith("ping-")
    await gateway.stop()


@pytest.mark.asyncio
async def test_probe_classifies_auth_rejection_and_closes() -> None:
    session, socket = _success_session()

    async def reject(payload: dict[str, Any]) -> None:
        await socket.push(
            {
                "headers": {"req_id": payload["headers"]["req_id"]},
                "errcode": 40001,
                "errmsg": "bad credentials",
            }
        )

    socket.on_send = reject

    with pytest.raises(WecomAuthenticationError, match="rejected"):
        await probe_wecom_credentials(
            "bot-1",
            "secret-1",
            session_factory=lambda: session,
            timeout=0.1,
        )

    assert socket.closed
    assert session.closed


@pytest.mark.asyncio
async def test_probe_classifies_timeout_as_unavailable_and_closes() -> None:
    socket = _FakeSocket()
    session = _FakeSession(socket)

    with pytest.raises(WecomUnavailableError, match="unavailable"):
        await probe_wecom_credentials(
            "bot-1",
            "secret-1",
            session_factory=lambda: session,
            timeout=0.01,
        )

    assert socket.closed
    assert session.closed
