"""Tests for the long-connection card.action.trigger SDK handler glue.

These tests directly exercise the `_lc_handle_card_action` async helper
(the function the SDK handler invokes) — they don't spin up a real
WebSocket, just verify the SDK→handler envelope conversion and the
toast → CallBackToast mapping.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close


@pytest.mark.asyncio
async def test_lc_handler_builds_envelope_and_calls_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im.feishu import long_connection as lc

    seen: list[dict[str, Any]] = []

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        seen.append(envelope)
        return True, None

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    # Build a fake SDK event payload (duck-typed, only the fields the handler reads).
    class _Op:
        open_id = "ou_user_1"

    class _Action:
        value = {
            "action": "ask_user",
            "run_id": "run_1",
            "choice": "yes",
            "question_id": "q_1",
        }

    class _Data:
        operator = _Op()
        token = "tok_xyz"
        action = _Action()

    class _Event:
        event = _Data()

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubeplex-dev"
    )
    # No toast → response.toast is None or a CallBackToast with empty content
    assert response is not None
    # The LC shim now mirrors the click token at event.token (where the HTTP
    # webhook path reads it) and keeps a copy at header.token for legacy
    # callers — the replay guard in _handle_card_action reads event.token.
    assert seen == [
        {
            "header": {"event_type": "card.action.trigger", "token": "tok_xyz"},
            "event": {
                "token": "tok_xyz",
                "operator": {"open_id": "ou_user_1"},
                "action": {
                    "value": {
                        "action": "ask_user",
                        "run_id": "run_1",
                        "choice": "yes",
                        "question_id": "q_1",
                    }
                },
            },
        }
    ]
    # The shape that the SDK wants back; no toast in this case.
    assert response.toast is None


@pytest.mark.asyncio
async def test_lc_handler_carries_toast_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im.feishu import long_connection as lc

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        return True, "这不是发给你的"

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    class _Op:
        open_id = "ou_user_1"

    class _Action:
        value = {"action": "ask_user", "run_id": "run_1", "choice": "yes"}

    class _Data:
        operator = _Op()
        token = "tok_t"
        action = _Action()

    class _Event:
        event = _Data()

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubeplex-dev"
    )
    assert response is not None
    assert response.toast is not None
    assert response.toast.content == "这不是发给你的"
    assert response.toast.type == "info"


@pytest.mark.asyncio
async def test_lc_handler_tolerates_missing_event_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a malformed SDK event shouldn't crash the dispatcher."""
    from cubeplex.im.feishu import long_connection as lc

    called: list[Any] = []

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        called.append(envelope)
        return True, "未知操作"

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    class _Event:
        event = None  # SDK passes None when the payload is missing

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubeplex-dev"
    )
    # The handler is still invoked with an empty envelope; downstream
    # parse_action_payload raises InvalidAction → toast "未知操作".
    assert response is not None
    assert called[0]["event"] == {}


@pytest.mark.asyncio
async def test_long_connection_disconnect_disables_reconnect_and_closes_sdk_client() -> None:
    from cubeplex.im.feishu.long_connection import FeishuLongConnection

    class _Client:
        def __init__(self) -> None:
            self._auto_reconnect = True
            self.disconnected = False

        async def _disconnect(self) -> None:
            self.disconnected = True

    client = _Client()
    lc = object.__new__(FeishuLongConnection)
    lc._client = client
    lc._thread_loop = None
    lc._ws_future = None

    await lc.disconnect()

    assert client._auto_reconnect is False
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_long_connection_disconnect_runs_sdk_disconnect_on_thread_loop() -> None:
    from cubeplex.im.feishu.long_connection import FeishuLongConnection

    loop_ready = threading.Event()
    worker_loop = asyncio.new_event_loop()

    def run_loop() -> None:
        asyncio.set_event_loop(worker_loop)
        loop_ready.set()
        worker_loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert loop_ready.wait(timeout=1.0)

    class _Client:
        def __init__(self) -> None:
            self._auto_reconnect = True
            self.disconnect_loop: asyncio.AbstractEventLoop | None = None

        async def _disconnect(self) -> None:
            self.disconnect_loop = asyncio.get_running_loop()

    client = _Client()
    lc = object.__new__(FeishuLongConnection)
    lc._client = client
    lc._thread_loop = worker_loop
    lc._ws_future = None

    try:
        await lc.disconnect()

        assert client._auto_reconnect is False
        assert client.disconnect_loop is worker_loop
    finally:
        if worker_loop.is_running():
            worker_loop.call_soon_threadsafe(worker_loop.stop)
        thread.join(timeout=1.0)
        worker_loop.close()


@pytest.mark.asyncio
async def test_graceful_receiver_suppresses_expected_shutdown_close() -> None:
    from cubeplex.im.feishu.long_connection import _install_graceful_shutdown_receiver

    class _Conn:
        async def recv(self) -> bytes:
            close = Close(1000, "bye")
            raise ConnectionClosedOK(close, close, False)

    class _Client:
        def __init__(self) -> None:
            self._conn = _Conn()
            self._auto_reconnect = False
            self.disconnected = False

        async def _disconnect(self) -> None:
            self.disconnected = True
            self._conn = None

    client = _Client()
    _install_graceful_shutdown_receiver(client, is_shutting_down=lambda: True)

    await client._receive_message_loop()

    assert client.disconnected is True
