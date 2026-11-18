"""Unit tests for IM background-notice helpers, recalled-reply fallback, and tailer stop."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest

from cubeplex.im.feishu.connector import FeishuConnector
from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher
from cubeplex.im.outbound import OutboundRunTailer
from cubeplex.im.run_handoff import (
    background_notice_event_id,
    extract_thread_id_from_scope_key,
    parse_background_notice_id,
)
from cubeplex.im.types import RenderState
from cubeplex.streams.run_events import append_run_event, create_run, update_run_meta


def test_round_trip_background_notice_event_id() -> None:
    event_id = background_notice_event_id("bgte-123")
    assert event_id == "background_notice:bgte-123"
    assert parse_background_notice_id(event_id) == "bgte-123"


def test_parse_background_notice_id_rejects_malformed() -> None:
    assert parse_background_notice_id("background_notice:") is None
    assert parse_background_notice_id("background_notice:bgte-123:extra") is None
    assert parse_background_notice_id("schedule:run-1") is None


def test_extract_thread_id_from_scope_key() -> None:
    assert extract_thread_id_from_scope_key("t:1700.123") == "1700.123"
    assert extract_thread_id_from_scope_key("u:U111|t:1700.456") == "1700.456"
    assert extract_thread_id_from_scope_key("t:") is None
    assert extract_thread_id_from_scope_key("u:U111|t:") is None
    assert extract_thread_id_from_scope_key("dm") is None
    assert extract_thread_id_from_scope_key("u:U111") is None


@pytest.mark.asyncio
async def test_feishu_recalled_reply_target_falls_back_to_channel_create() -> None:
    reply_calls: list[Any] = []
    create_calls: list[Any] = []

    def _reply(req: Any) -> Any:
        reply_calls.append(req)
        return SimpleNamespace(success=lambda: False, code=230011, msg="message recalled", raw=None)

    def _create(req: Any) -> Any:
        create_calls.append(req)
        return SimpleNamespace(
            success=lambda: True,
            code=0,
            msg="ok",
            data=SimpleNamespace(message_id=f"om_created_{len(create_calls)}"),
        )

    fake_client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(message=SimpleNamespace(reply=_reply, create=_create))
        )
    )

    connector = FeishuConnector(
        bot_open_id="ou_bot",
        client=fake_client,
        channel_id="oc_group_1",
        reply_to_id="om_recalled_1",
    )
    msg_id = await connector.send_card_init_message("card_1")
    assert msg_id == "om_created_1"
    assert len(reply_calls) == 1
    assert len(create_calls) == 1

    # Emergency text and send_to_chat also fall back when reply_to_id is rejected.
    connector2 = FeishuConnector(
        bot_open_id="ou_bot",
        client=fake_client,
        channel_id="oc_group_1",
        reply_to_id="om_recalled_2",
    )
    assert await connector2._send_emergency_text("fallback emergency") == "om_created_2"
    assert (
        await connector2.send_to_chat("oc_group_1", "om_recalled_3", "fallback chat")
        == "om_created_3"
    )
    assert len(reply_calls) == 3
    assert len(create_calls) == 3


@pytest.mark.asyncio
async def test_outbound_tailer_exits_cleanly_when_run_is_cancelled() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prefix = "unit-im-cancel"
    run_id = "run-cancelled-1"
    conversation_id = "conv-cancelled-1"

    await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        status="running",
        started_at="2026-11-18T00:00:00Z",
        user_message="hi",
        ttl_seconds=60,
    )
    await append_run_event(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        payload={"type": "text_delta", "data": {"content": "partial"}},
        ttl_seconds=60,
        maxlen=100,
    )

    connector = SimpleNamespace(
        on_processing_start=lambda _s: asyncio.sleep(0),
        on_processing_complete=lambda _s: asyncio.sleep(0),
        on_processing_failed=lambda _s: asyncio.sleep(0),
        send_card_init_message=lambda _cid: asyncio.sleep(0, result="om_1"),
        _send_emergency_text=lambda _t: asyncio.sleep(0),
    )
    cardkit = SimpleNamespace(
        create_entity=lambda _c: asyncio.sleep(0, result="AAQA-1"),
        stream_text=lambda **_kw: asyncio.sleep(0),
        patch_card=lambda **_kw: asyncio.sleep(0),
        finalize=lambda **_kw: asyncio.sleep(0, result=True),
        aclose=lambda: asyncio.sleep(0),
    )
    state = RenderState(bot_name="CubePlex", run_id=run_id)
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)
    tailer = OutboundRunTailer(
        redis=redis,
        key_prefix=prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=50,
    )
    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.08)
    await update_run_meta(redis, prefix=prefix, run_id=run_id, status="cancelled")

    await asyncio.wait_for(task, timeout=2.0)
    await redis.aclose()
