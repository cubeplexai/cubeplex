"""Regression tests for FeishuLongConnection worker-loop isolation and teardown.

Protects five invariants when running ``lark_oapi.ws.Client`` workers:
1. Concurrent ``FeishuLongConnection`` startups never cross-wire ``_conn`` and
   ``_receive_message_loop`` across different worker event loops via
   ``lark_oapi.ws.client.loop``.
2. ``lark.ws.Client`` (and its ``ExpiringCache`` background task) is bound to
   the connection's own worker loop rather than the main FastAPI event loop.
3. Standing long-connection workers do not occupy slots in the main loop's
   default ``ThreadPoolExecutor`` used by ``asyncio.to_thread``.
4. ``disconnect()`` finishes multi-tick async WebSocket ``close()`` without
   raising ``CancelledError`` and cleanly aborts in-flight ``_connect()``.
5. Terminal reconnect failures in ``_receive_message_loop`` exit the worker so
   ``is_open()`` transitions to ``False`` instead of leaving a zombie state.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import lark_oapi.ws.client as ws_client_mod
import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from cubeplex.im.feishu.long_connection import FeishuLongConnection


class _LoopBoundFakeConnection:
    """Minimal WebSocket double whose waiters bind to the creation loop,
    matching ``websockets.asyncio.client.ClientConnection``."""

    def __init__(self, *, fail_recv_after_open: bool = False) -> None:
        self.created_loop = asyncio.get_running_loop()
        self.recv_loop: asyncio.AbstractEventLoop | None = None
        self.recv_entered = threading.Event()
        self.close_completed = False
        self._fail_recv_after_open = fail_recv_after_open
        self._closed_waiter: asyncio.Future[None] = self.created_loop.create_future()

    async def recv(self) -> bytes:
        self.recv_loop = asyncio.get_running_loop()
        self.recv_entered.set()
        if self._fail_recv_after_open:
            close = Close(1006, "abnormal")
            raise ConnectionClosedError(close, close, False)
        # Mirrors websockets SimpleQueue.get_waiter: awaiting a Future created
        # on ``self.created_loop`` raises RuntimeError immediately if ``recv``
        # was scheduled onto another thread's event loop.
        await asyncio.shield(self._closed_waiter)
        close = Close(1000, "bye")
        raise ConnectionClosedOK(close, close, False)

    async def send(self, data: bytes) -> None:
        _ = data

    async def close(self) -> None:
        if asyncio.get_running_loop() is not self.created_loop:
            # Mirror websockets Connection.send_context awaiting a loop-bound
            # connection_lost_waiter on close().
            await asyncio.shield(self._closed_waiter)
        # Suspend across loop ticks like a real WebSocket close handshake so
        # premature loop teardown / task cancellation is caught immediately.
        await asyncio.sleep(0.01)
        if not self._closed_waiter.done():
            self._closed_waiter.set_result(None)
        self.close_completed = True


def _make_long_connection(account_id: str) -> FeishuLongConnection:
    async def _noop_ingest(event: Any, *, account: Any, session_maker: Any, **_: Any) -> Any:
        _ = (event, account, session_maker)
        return SimpleNamespace(outcome="enqueued")

    return FeishuLongConnection(
        account=SimpleNamespace(id=account_id, external_account_id=f"cli_{account_id}"),
        app_id=f"cli_{account_id}",
        app_secret="secret",
        bot_open_id=f"ou_{account_id}",
        ingest=_noop_ingest,
        session_maker=None,
        run_manager=None,
        redis_key_prefix="test",
    )


def _capture_worker_loop_errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    loop_errors: list[str] = []
    orig_new_event_loop = asyncio.new_event_loop

    def _instrumented_loop() -> asyncio.AbstractEventLoop:
        loop = orig_new_event_loop()

        def _handler(_loop: asyncio.AbstractEventLoop, ctx: dict[str, Any]) -> None:
            exc = ctx.get("exception")
            msg = ctx.get("message", "")
            loop_errors.append(f"{msg}: {exc!r}")

        loop.set_exception_handler(_handler)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", _instrumented_loop)
    monkeypatch.setattr(ws_client_mod, "loop", ws_client_mod.loop)
    monkeypatch.setattr(
        ws_client_mod.Client,
        "_get_conn_url",
        lambda self: f"wss://msg-frontier.feishu.cn/ws/v2?device_id={self._app_id}&service_id=1",
    )
    return loop_errors


@pytest.mark.asyncio
async def test_concurrent_long_connections_isolate_worker_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_loop = asyncio.get_running_loop()
    first_ws_created = threading.Event()
    second_ws_entered = threading.Event()
    connections: list[_LoopBoundFakeConnection] = []
    conn_lock = threading.Lock()
    loop_errors = _capture_worker_loop_errors(monkeypatch)

    async def _fake_ws_connect(url: str, **kwargs: Any) -> _LoopBoundFakeConnection:
        _ = (url, kwargs)
        conn = _LoopBoundFakeConnection()
        with conn_lock:
            idx = len(connections)
            connections.append(conn)
        if idx == 0:
            first_ws_created.set()
            # Hold account 1 inside _connect() until account 2 has started its
            # worker loop and entered _connect(), deterministically triggering
            # any shared ws_client_mod.loop overwrite before loop.create_task().
            await asyncio.to_thread(second_ws_entered.wait, 2.0)
        else:
            second_ws_entered.set()
        return conn

    monkeypatch.setattr(ws_client_mod.websockets, "connect", _fake_ws_connect)

    lc_1 = _make_long_connection("acc_1")
    lc_2 = _make_long_connection("acc_2")

    try:
        await lc_1.connect()
        assert await asyncio.to_thread(first_ws_created.wait, 2.0)
        await lc_2.connect()

        assert await asyncio.to_thread(second_ws_entered.wait, 2.0)
        for conn in connections:
            assert await asyncio.to_thread(conn.recv_entered.wait, 2.0)

        assert loop_errors == [], f"worker loop raised unhandled errors: {loop_errors}"
        assert len(connections) == 2
        for conn in connections:
            assert conn.recv_loop is conn.created_loop

        assert lc_1._thread_loop is not None
        assert lc_2._thread_loop is not None
        assert lc_1._thread_loop is not lc_2._thread_loop
        assert lc_1._client._cache._cron.get_loop() is lc_1._thread_loop
        assert lc_2._client._cache._cron.get_loop() is lc_2._thread_loop
        assert lc_1._client._cache._cron.get_loop() is not main_loop
    finally:
        await lc_1.disconnect()
        await lc_2.disconnect()

    assert not lc_1.is_open()
    assert not lc_2.is_open()
    for conn in connections:
        assert conn.close_completed
    assert lc_1._thread is not None and not lc_1._thread.is_alive()
    assert lc_2._thread is not None and not lc_2._thread.is_alive()
    assert loop_errors == []


@pytest.mark.asyncio
async def test_long_connection_does_not_starve_default_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    conn_ready = asyncio.Event()
    _capture_worker_loop_errors(monkeypatch)

    async def _fake_ws_connect(url: str, **kwargs: Any) -> _LoopBoundFakeConnection:
        _ = (url, kwargs)
        conn = _LoopBoundFakeConnection()
        loop.call_soon_threadsafe(conn_ready.set)
        return conn

    monkeypatch.setattr(ws_client_mod.websockets, "connect", _fake_ws_connect)

    orig_executor = getattr(loop, "_default_executor", None)
    tiny_executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(tiny_executor)

    lc = _make_long_connection("acc_exec")
    try:
        try:
            await lc.connect()
            await asyncio.wait_for(conn_ready.wait(), timeout=2.0)
            # With a 1-worker default executor, a long-connection running inside
            # run_in_executor(None, ...) permanently starves asyncio.to_thread.
            result = await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), timeout=0.5)
            assert result == "ok"
        finally:
            await asyncio.wait_for(lc.disconnect(), timeout=1.0)
    finally:
        loop._default_executor = orig_executor  # type: ignore[attr-defined]
        tiny_executor.shutdown(wait=False, cancel_futures=True)

    assert lc._thread is not None and not lc._thread.is_alive()


@pytest.mark.asyncio
async def test_disconnect_aborts_inflight_connect_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    connect_entered = asyncio.Event()
    loop_errors = _capture_worker_loop_errors(monkeypatch)

    async def _hanging_ws_connect(url: str, **kwargs: Any) -> _LoopBoundFakeConnection:
        _ = (url, kwargs)
        loop.call_soon_threadsafe(connect_entered.set)
        await asyncio.Future()
        raise AssertionError("unreachable")

    monkeypatch.setattr(ws_client_mod.websockets, "connect", _hanging_ws_connect)

    lc = _make_long_connection("acc_inflight")
    await lc.connect()
    await asyncio.wait_for(connect_entered.wait(), timeout=2.0)
    await asyncio.wait_for(lc.disconnect(), timeout=1.0)

    assert not lc.is_open()
    assert lc._thread is not None and not lc._thread.is_alive()
    assert loop_errors == []


@pytest.mark.asyncio
async def test_terminal_reconnect_failure_marks_connection_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_errors = _capture_worker_loop_errors(monkeypatch)

    async def _fake_ws_connect(url: str, **kwargs: Any) -> _LoopBoundFakeConnection:
        _ = (url, kwargs)
        return _LoopBoundFakeConnection(fail_recv_after_open=True)

    async def _terminal_reconnect(self: Any) -> None:
        _ = self
        raise ws_client_mod.ClientException(403, "credentials revoked")

    monkeypatch.setattr(ws_client_mod.websockets, "connect", _fake_ws_connect)
    monkeypatch.setattr(ws_client_mod.Client, "_reconnect", _terminal_reconnect)

    lc = _make_long_connection("acc_revoked")
    try:
        await lc.connect()
        assert lc._ws_future is not None
        await asyncio.wait_for(asyncio.shield(lc._ws_future), timeout=2.0)
        assert not lc.is_open()
        assert loop_errors == []
    finally:
        await lc.disconnect()
