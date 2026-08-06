"""DingTalk stream must respect asyncio cancellation (no Ctrl-C hang)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.im.dingtalk.gateway import DingtalkGateway, stream_until_disconnect


@pytest.mark.asyncio
async def test_stream_until_disconnect_propagates_cancelled_error() -> None:
    """SDK start() swallows CancelledError; our wrapper must not."""
    client = MagicMock()
    client.pre_start = MagicMock()
    client.open_connection = MagicMock(
        return_value={
            "endpoint": "wss://example.test/connect",
            "ticket": "t1",
        }
    )

    async def _keepalive(_ws: object) -> None:
        await asyncio.Event().wait()

    client.keepalive = _keepalive
    client.websocket = None

    class _FakeWs:
        def __init__(self) -> None:
            self._q: asyncio.Queue[str | None] = asyncio.Queue()

        async def __aenter__(self) -> _FakeWs:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def __aiter__(self) -> _FakeWs:
            return self

        async def __anext__(self) -> str:
            item = await self._q.get()
            if item is None:
                raise StopAsyncIteration
            return item

        async def close(self) -> None:
            await self._q.put(None)

    fake_ws = _FakeWs()

    with patch("cubeplex.im.dingtalk.gateway.websockets.connect", return_value=fake_ws):
        task = asyncio.create_task(stream_until_disconnect(client))
        await asyncio.sleep(0)  # let it enter async for
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_gateway_stop_cancels_run_task_quickly() -> None:
    """stop() must finish without waiting on SDK reconnect sleep."""
    account = SimpleNamespace(id="acc_1", external_account_id="ext_1", workspace_id="ws_1")
    gw = DingtalkGateway(
        account=account,
        app_key="k",
        app_secret="s",
        ingest=AsyncMock(),
        session_maker=MagicMock(),
        run_manager=None,
        redis_key_prefix="test",
    )

    async def _hang_forever(_client: Any) -> None:
        await asyncio.Event().wait()

    with (
        patch("cubeplex.im.dingtalk.gateway.stream_until_disconnect", side_effect=_hang_forever),
        patch("cubeplex.im.dingtalk.gateway.dingtalk_stream") as mock_sdk,
    ):
        mock_sdk.Credential = MagicMock()
        mock_sdk.DingTalkStreamClient = MagicMock(
            return_value=MagicMock(
                register_callback_handler=MagicMock(),
                websocket=None,
            )
        )
        mock_sdk.ChatbotMessage.TOPIC = "topic"
        await gw.start()
        assert gw._task is not None and not gw._task.done()

        await asyncio.wait_for(gw.stop(), timeout=2.0)
        assert gw._task is None or gw._task.done()
