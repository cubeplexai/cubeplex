"""End-to-end IM chain test (Task 14).

Two halves are covered separately because the LLM run in the middle is
genuinely unsimulatable in CI:

1. Inbound -> queue: covered by ``tests/e2e/test_im_feishu_ingress.py``
   (signed body lands in the durable queue).

2. Outbound: this test seeds a synthetic run event stream into Redis (the
   same stream the real ``RunManager`` writes to), tails it with the real
   ``OutboundRunTailer`` against a recording connector + a recording
   CardKit fake, and asserts the render fold + reaction lifecycle behave
   end-to-end.
"""

from __future__ import annotations

import asyncio
import secrets as _secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubebox.config import config as _cubebox_config
from cubebox.im.feishu.op_dispatcher import FeishuOpDispatcher
from cubebox.im.outbound import OutboundRunTailer
from cubebox.im.types import RenderState
from cubebox.streams.run_events import append_run_event

pytestmark = pytest.mark.asyncio


class _RecordingConnector:
    """Stand-in connector that records every call the tailer makes.

    Mirrors the FeishuConnector hook + send/edit surface — what the tailer
    actually depends on.
    """

    def __init__(self) -> None:
        self.card_init_calls: list[str] = []
        self.emergency_text: list[str] = []
        self.processing_started = False
        self.processing_completed = False
        self.processing_failed = False

    async def on_processing_start(self, state: RenderState) -> None:
        self.processing_started = True

    async def on_processing_complete(self, state: RenderState) -> None:
        self.processing_completed = True

    async def on_processing_failed(self, state: RenderState) -> None:
        self.processing_failed = True

    async def send_card_init_message(self, card_id: str) -> str:
        self.card_init_calls.append(card_id)
        return f"om_card_msg_{len(self.card_init_calls)}"

    async def _send_emergency_text(self, text: str) -> None:
        self.emergency_text.append(text)


class _RecordingCardKit:
    """Stand-in CardKitClient that records every call the tailer makes.

    Mirrors the four entry points the tailer drives — create_entity,
    stream_text, patch_card, finalize — and exposes the recorded payloads
    for assertion. No network. Each method returns the value
    ``OutboundRunTailer._dispatch_op`` expects so the lifecycle proceeds.
    """

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.streamed: list[tuple[str, str, str]] = []  # (card_id, element_id, content)
        self.patched: list[dict[str, Any]] = []
        self.finalized: list[dict[str, Any]] = []
        self.aclose_called = False

    async def create_entity(self, card_json: dict[str, Any]) -> str:
        self.created.append(card_json)
        return f"AAQA-{len(self.created)}"

    async def stream_text(
        self, *, card_id: str, element_id: str, content: str, sequence: int
    ) -> None:
        self.streamed.append((card_id, element_id, content))

    async def patch_card(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> None:
        self.patched.append(card_json)

    async def finalize(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> bool:
        self.finalized.append(card_json)
        return True

    async def aclose(self) -> None:
        self.aclose_called = True


@pytest_asyncio.fixture
async def _redis() -> AsyncIterator[Redis]:
    # Production app sets decode_responses=True (cubebox/api/app.py); match it
    # so the Lua-written 'payload' string field comes back as a str key on xread.
    client: Redis = Redis.from_url(
        _cubebox_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def test_outbound_tailer_consumes_real_redis_stream_to_completion(
    _redis: Redis,
) -> None:
    """A short run streams text + a tool + done; the tailer must create the
    card, stream cumulative text, patch on tool_call, finalize on done, and
    drive the processing-start/complete lifecycle.
    """
    prefix = f"e2e-im-{_secrets.token_hex(4)}"
    run_id = f"run-e2e-{_secrets.token_hex(4)}"
    conversation_id = "conv-e2e"

    connector = _RecordingConnector()
    cardkit = _RecordingCardKit()
    state = RenderState(bot_name="cubebox", run_id=run_id, inbound_message_id="om_inbound_e2e")
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)
    tailer = OutboundRunTailer(
        redis=_redis,
        key_prefix=prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=200,
    )
    tailer_task = asyncio.create_task(tailer.run())

    common: dict[str, Any] = {
        "prefix": prefix,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "ttl_seconds": 120,
        "maxlen": 1000,
    }

    # 1) First text_delta — card is created.
    await append_run_event(
        _redis, payload={"type": "text_delta", "data": {"content": "Hello"}}, **common
    )
    # Force the debounce window to elapse so the next text_delta produces a stream op.
    await asyncio.sleep(1.0)
    await append_run_event(
        _redis, payload={"type": "text_delta", "data": {"content": " world"}}, **common
    )
    # 2) tool_call — triggers a patch_card.
    await append_run_event(
        _redis,
        payload={"type": "tool_call", "data": {"tool_call_id": "t1", "name": "calc"}},
        **common,
    )
    # 3) Terminal `done`.
    await append_run_event(_redis, payload={"type": "done", "data": {}}, **common)

    await asyncio.wait_for(tailer_task, timeout=10.0)

    assert connector.processing_started
    assert connector.processing_completed
    assert not connector.processing_failed
    assert len(cardkit.created) == 1, "expected exactly one card create"
    assert connector.card_init_calls == ["AAQA-1"]
    # Cumulative text gets streamed on the second un-throttled delta.
    assert any(content == "Hello world" for _, _, content in cardkit.streamed)
    # tool_call drove a patch.
    assert len(cardkit.patched) >= 1
    # done drove a finalize.
    assert len(cardkit.finalized) == 1
    # Tailer's finally-block released the CardKit HTTP pool.
    assert cardkit.aclose_called


async def test_outbound_tailer_emits_failure_on_error_event(
    _redis: Redis,
) -> None:
    prefix = f"e2e-im-err-{_secrets.token_hex(4)}"
    run_id = f"run-err-{_secrets.token_hex(4)}"
    conversation_id = "conv-err"

    connector = _RecordingConnector()
    cardkit = _RecordingCardKit()
    state = RenderState(bot_name="cubebox", run_id=run_id, inbound_message_id="om_inbound_err")
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)
    tailer = OutboundRunTailer(
        redis=_redis,
        key_prefix=prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=200,
    )
    tailer_task = asyncio.create_task(tailer.run())

    await append_run_event(
        _redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        payload={"type": "error", "data": {"message": "boom"}},
        ttl_seconds=120,
        maxlen=1000,
    )

    await asyncio.wait_for(tailer_task, timeout=10.0)

    assert connector.processing_started
    assert connector.processing_failed
    assert not connector.processing_completed
    # No card was ever created (error event arrived without any text_delta first),
    # so the tailer must surface the error via emergency text.
    assert any("boom" in t for t in connector.emergency_text)
