"""Tailer coalesces consecutive stream_text ops so catch-up is not N×chat.update."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubeplex.im.outbound import OutboundOp, OutboundRunTailer
from cubeplex.im.types import RenderState


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.ops: list[OutboundOp] = []

    async def dispatch_create(self, state: Any) -> bool:
        self.ops.append(OutboundOp(kind="card_create"))
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        self.ops.append(OutboundOp(kind="stream_text", text=text))
        return True

    async def dispatch_patch(self, state: Any) -> bool:
        self.ops.append(OutboundOp(kind="patch_card"))
        return True

    async def dispatch_finalize(self, state: Any) -> bool:
        self.ops.append(OutboundOp(kind="finalize", final=True))
        return True

    async def aclose(self) -> None:
        return None


class _FakeConnector:
    async def on_processing_start(self, state: RenderState) -> None:
        return None

    async def on_processing_complete(self, state: RenderState) -> None:
        return None

    async def on_processing_failed(self, state: RenderState) -> None:
        return None


class _FakeEvent:
    def __init__(self, event_id: str, payload: dict[str, Any]) -> None:
        self.event_id = event_id
        self.payload = payload


@pytest.mark.asyncio
async def test_tailer_coalesces_stream_text_before_finalize() -> None:
    """Many stream ops then finalize → only finalize is dispatched for text."""
    state = RenderState(bot_name="bot", run_id="run-1", stream_interval=0.0)
    state.card_id = "card-1"
    dispatcher = _RecordingDispatcher()
    connector = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=AsyncMock(),
        key_prefix="cb-",
        run_id="run-1",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=1,
    )

    events = [
        _FakeEvent("1", {"type": "text_delta", "data": {"content": "Hello "}}),
        _FakeEvent("2", {"type": "text_delta", "data": {"content": "world"}}),
        _FakeEvent("3", {"type": "text_delta", "data": {"content": "!"}}),
        _FakeEvent("4", {"type": "done", "data": {}}),
    ]

    async def _read(*_a: Any, **_k: Any) -> list[_FakeEvent]:
        # First call returns the batch; second would block — stop via done.
        nonlocal events
        batch, events = events, []
        return batch

    import cubeplex.im.outbound as outbound_mod

    original = outbound_mod.read_run_events_after
    outbound_mod.read_run_events_after = _read  # type: ignore[assignment]
    try:
        await tailer.run()
    finally:
        outbound_mod.read_run_events_after = original  # type: ignore[assignment]

    kinds = [op.kind for op in dispatcher.ops]
    # No intermediate stream_text — finalize carries the full buffer.
    assert "stream_text" not in kinds
    assert kinds[-1] == "finalize"
    assert state.card_state.streaming_content == "Hello world!"


@pytest.mark.asyncio
async def test_tailer_flushes_coalesced_stream_when_no_terminal() -> None:
    state = RenderState(bot_name="bot", run_id="run-2", stream_interval=0.0)
    state.card_id = "card-1"
    dispatcher = _RecordingDispatcher()
    connector = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=AsyncMock(),
        key_prefix="cb-",
        run_id="run-2",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=1,
    )

    batches = [
        [
            _FakeEvent("1", {"type": "text_delta", "data": {"content": "A"}}),
            _FakeEvent("2", {"type": "text_delta", "data": {"content": "B"}}),
        ],
        [
            _FakeEvent("3", {"type": "done", "data": {}}),
        ],
    ]
    call = {"n": 0}

    async def _read(*_a: Any, **_k: Any) -> list[_FakeEvent]:
        i = call["n"]
        call["n"] += 1
        if i < len(batches):
            return batches[i]
        return []

    import cubeplex.im.outbound as outbound_mod

    original = outbound_mod.read_run_events_after
    outbound_mod.read_run_events_after = _read  # type: ignore[assignment]
    try:
        await tailer.run()
    finally:
        outbound_mod.read_run_events_after = original  # type: ignore[assignment]

    kinds = [op.kind for op in dispatcher.ops]
    # First batch: one coalesced stream; second: finalize (no extra stream).
    assert kinds.count("stream_text") == 1
    assert kinds[-1] == "finalize"
    assert state.card_state.streaming_content == "AB"
