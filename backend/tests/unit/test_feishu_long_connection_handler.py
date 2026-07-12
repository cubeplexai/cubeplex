"""Unit tests for the Feishu long-connection dispatch lambda (Task 7).

These verify the thread → asyncio bridge: when the SDK fires _on_message
on a worker thread, the handler must reconstruct the webhook envelope, run
parse_inbound through FeishuConnector, and dispatch the resulting
InboundEvent via run_coroutine_threadsafe against the captured loop.

The SDK client itself is not exercised here — that's the unsimulatable
network boundary covered by the manual smoke checklist in Task 16.
"""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.im.feishu.long_connection import build_event_handler

pytestmark = pytest.mark.asyncio


def _make_fake_session_maker() -> Any:
    """Return a session_maker whose session always returns no binding row."""

    @asynccontextmanager
    async def _session_ctx() -> Any:  # type: ignore[misc]
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute.return_value = result
        yield session

    return _session_ctx


def _sdk_event_object() -> Any:
    """Mimic the SDK's typed P2ImMessageReceiveV1 by giving the marshal helper
    a JSON-serializable object that exposes ``.header`` and ``.event``.

    lark_oapi.JSON.marshal walks ``__dict__``, so a SimpleNamespace works.
    """
    sender = SimpleNamespace(
        sender_id=SimpleNamespace(open_id="ou_user", union_id="on_user", user_id=None),
        sender_type="user",
        tenant_key="t1",
    )
    message = SimpleNamespace(
        message_id="om_msg_long",
        chat_id="oc_dm",
        chat_type="p2p",
        message_type="text",
        content=json.dumps({"text": "hello"}),
        mentions=None,
        root_id=None,
        parent_id=None,
        thread_id=None,
    )
    event = SimpleNamespace(sender=sender, message=message)
    header = SimpleNamespace(
        event_id="ev_long_1",
        event_type="im.message.receive_v1",
        token=None,
        create_time=None,
        tenant_key="t1",
        app_id="cli_test",
    )
    return SimpleNamespace(event=event, header=header)


async def test_handler_marshals_envelope_and_dispatches_ingest_via_threadsafe() -> None:
    captured: list[dict[str, Any]] = []
    barrier = asyncio.Event()

    class _Outcome:
        outcome = "enqueued"

    async def fake_ingest(event: Any, *, account: Any, session_maker: Any) -> Any:
        captured.append(
            {
                "platform_event_id": event.platform_event_id,
                "channel_id": event.channel_id,
                "scope_key": event.scope_key,
                "text": event.text,
                "account_external_id": event.account_external_id,
            }
        )
        barrier.set()
        return _Outcome()

    account = SimpleNamespace(id="acc_test", external_account_id="cli_test")
    loop = asyncio.get_running_loop()
    handler = build_event_handler(
        account=account,
        bot_open_id="ou_bot",
        ingest=fake_ingest,
        session_maker=_make_fake_session_maker(),
        loop=loop,
        run_manager=None,
        redis_key_prefix="test",
    )

    # Reach into the dispatcher to find the registered receive_v1 callback.
    # lark_oapi exposes it via ``handler._callback_map[event_type]`` — but
    # since the SDK shape may change, we round-trip through the builder by
    # rebuilding a dispatcher locally if needed. To keep this test stable,
    # call the long-connection module's internal function directly via the
    # dispatcher we just built.
    on_message = _find_p2_message_receive_handler(handler)
    assert on_message is not None, "could not locate p2 message receive handler"

    sdk_event = _sdk_event_object()

    # Drive _on_message on a NON-asyncio thread — that's where the SDK runs.
    def _drive_from_thread() -> None:
        on_message(sdk_event)

    t = threading.Thread(target=_drive_from_thread)
    t.start()
    await asyncio.wait_for(barrier.wait(), timeout=2.0)
    t.join(timeout=2.0)

    assert len(captured) == 1
    record = captured[0]
    assert record["platform_event_id"] == "ev_long_1"
    assert record["channel_id"] == "oc_dm"
    assert record["scope_key"] == "dm"
    assert record["text"] == "hello"
    # The long-connection module fills account_external_id from the bound account.
    assert record["account_external_id"] == "cli_test"


async def test_handler_drops_event_when_parser_returns_none() -> None:
    """A bot-echo (sender_type='app') must not reach ingest at all."""
    captured: list[Any] = []

    async def fake_ingest(event: Any, *, account: Any, session_maker: Any) -> Any:
        captured.append(event)
        return SimpleNamespace(outcome="enqueued")

    account = SimpleNamespace(id="acc_test", external_account_id="cli_test")
    handler = build_event_handler(
        account=account,
        bot_open_id="ou_bot",
        ingest=fake_ingest,
        session_maker=_make_fake_session_maker(),
        loop=asyncio.get_running_loop(),
        run_manager=None,
        redis_key_prefix="test",
    )
    on_message = _find_p2_message_receive_handler(handler)
    assert on_message is not None

    sdk_event = _sdk_event_object()
    sdk_event.event.sender.sender_type = "app"

    def _drive_from_thread() -> None:
        on_message(sdk_event)

    t = threading.Thread(target=_drive_from_thread)
    t.start()
    t.join(timeout=2.0)
    # Give the loop a tick to confirm nothing was scheduled.
    await asyncio.sleep(0.05)
    assert captured == []


def _find_p2_message_receive_handler(handler: Any) -> Any:
    """Pull the registered callback off the SDK dispatcher.

    lark_oapi 1.6.x stores per-event processors in ``handler._processorMap``
    keyed by ``'p2.im.message.receive_v1'``; each processor wraps the
    registered callable in its ``.f`` attribute. If the SDK reorganizes
    this on upgrade the test fails loudly so the migration is obvious.
    """
    processor_map = getattr(handler, "_processorMap", None)
    if not processor_map:
        return None
    processor = processor_map.get("p2.im.message.receive_v1")
    if processor is None:
        return None
    return getattr(processor, "f", None)
