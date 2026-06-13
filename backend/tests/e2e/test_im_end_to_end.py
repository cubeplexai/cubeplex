"""End-to-end IM chain test (Task 14).

Two halves are covered separately because the LLM run in the middle is
genuinely unsimulatable in CI:

1. Inbound -> queue: covered by ``tests/e2e/test_im_feishu_ingress.py``
   (signed body lands in the durable queue).

2. Outbound: this test seeds a synthetic run event stream into Redis (the
   same stream the real ``RunManager`` writes to), tails it with the real
   ``OutboundRunTailer`` against a recording connector, and asserts the
   render fold + reaction lifecycle behave end-to-end.
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
from cubebox.im.outbound import OutboundRunTailer
from cubebox.im.types import RenderState
from cubebox.streams.run_events import append_run_event

pytestmark = pytest.mark.asyncio


class _RecordingConnector:
    """Stand-in connector that records every call the tailer makes.

    Mirrors the FeishuConnector hook + send/edit surface — what the tailer
    actually depends on. Used by Task 14 and by future per-platform tests.
    """

    def __init__(self) -> None:
        self.posts: list[str] = []
        self.edits: list[str] = []
        self.processing_started = False
        self.processing_completed = False
        self.processing_failed = False

    async def on_processing_start(self, state: RenderState) -> None:
        self.processing_started = True

    async def on_processing_complete(self, state: RenderState) -> None:
        self.processing_completed = True

    async def on_processing_failed(self, state: RenderState) -> None:
        self.processing_failed = True

    async def post_placeholder(self, text: str) -> str:
        self.posts.append(text)
        return f"om_reply_{len(self.posts)}"

    async def edit(self, message_id: str | None, text: str) -> None:
        self.edits.append(text)


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
    """A short run streams text + a tool + done; the tailer must post once,
    edit at least once, then complete the processing-start/complete lifecycle.
    """
    prefix = f"e2e-im-{_secrets.token_hex(4)}"
    run_id = f"run-e2e-{_secrets.token_hex(4)}"
    conversation_id = "conv-e2e"

    connector = _RecordingConnector()
    state = RenderState(bot_name="cubebox", run_id=run_id, inbound_message_id="om_inbound_e2e")
    tailer = OutboundRunTailer(
        redis=_redis,
        key_prefix=prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        cardkit=None,
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

    # 1) First text_delta — placeholder posted.
    await append_run_event(
        _redis, payload={"type": "text_delta", "data": {"content": "Hello"}}, **common
    )
    # Force the debounce window to elapse so the next text_delta produces an edit op.
    await asyncio.sleep(1.0)
    await append_run_event(
        _redis, payload={"type": "text_delta", "data": {"content": " world"}}, **common
    )
    # 2) tool_call coalesced into a status line.
    await append_run_event(
        _redis, payload={"type": "tool_call", "data": {"name": "calc"}}, **common
    )
    # 3) Terminal `done`.
    await append_run_event(_redis, payload={"type": "done", "data": {}}, **common)

    await asyncio.wait_for(tailer_task, timeout=10.0)

    assert connector.processing_started
    assert connector.processing_completed
    assert not connector.processing_failed
    assert len(connector.posts) == 1, "expected exactly one placeholder post"
    assert "Hello" in connector.posts[0]
    # Final edit carries the full composite (tools + text).
    assert any("Hello world" in t for t in connector.edits)
    assert any("calc" in t for t in connector.edits)


async def test_outbound_tailer_emits_failure_on_error_event(
    _redis: Redis,
) -> None:
    prefix = f"e2e-im-err-{_secrets.token_hex(4)}"
    run_id = f"run-err-{_secrets.token_hex(4)}"
    conversation_id = "conv-err"

    connector = _RecordingConnector()
    state = RenderState(bot_name="cubebox", run_id=run_id, inbound_message_id="om_inbound_err")
    tailer = OutboundRunTailer(
        redis=_redis,
        key_prefix=prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        cardkit=None,
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
    # No text_delta arrived — the post-fallback for terminal events fires so
    # the user sees the error notice.
    assert any("boom" in t or "error" in t.lower() for t in connector.posts)
