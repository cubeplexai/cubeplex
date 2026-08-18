"""Tailer coalesces consecutive stream_text ops so catch-up is not N×chat.update."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubeplex.im.outbound import OutboundOp, OutboundRunTailer
from cubeplex.im.types import RenderState


class _RecordingDispatcher:
    def __init__(self, *, segment: int = 0) -> None:
        self.ops: list[OutboundOp] = []
        # Mimic Slack multi-segment drain when segment > 0.
        self.sent_char_offset: int = 0
        self._segment = segment

    async def dispatch_create(self, state: Any) -> bool:
        self.ops.append(OutboundOp(kind="card_create"))
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        self.ops.append(OutboundOp(kind="stream_text", text=text))
        if self._segment > 0:
            # Advance one segment per call until the full buffer is covered.
            full = len(state.card_state.streaming_content or "")
            self.sent_char_offset = min(full, self.sent_char_offset + self._segment)
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


@pytest.mark.asyncio
async def test_tailer_drains_multi_segment_stream_before_wait() -> None:
    """Long coalesced buffer must be fully flushed (multiple stream calls)."""
    state = RenderState(bot_name="bot", run_id="run-3", stream_interval=0.0)
    state.card_id = "card-1"
    # 3 segments of 4 chars each → need 3 dispatch_stream calls to drain.
    dispatcher = _RecordingDispatcher(segment=4)
    connector = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=AsyncMock(),
        key_prefix="cb-",
        run_id="run-3",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        block_ms=1,
    )

    long = "abcdefghijkl"  # 12 chars
    batches = [
        [_FakeEvent("1", {"type": "text_delta", "data": {"content": long}})],
        [_FakeEvent("2", {"type": "done", "data": {}})],
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

    # First batch drains with 3 stream calls (12/4); finalize does not need more.
    stream_count = sum(1 for op in dispatcher.ops if op.kind == "stream_text")
    assert stream_count == 3
    assert dispatcher.sent_char_offset == 12
    assert dispatcher.ops[-1].kind == "finalize"


@pytest.mark.asyncio
async def test_tailer_flushes_presented_on_paused_hitl() -> None:
    """present_file must go out at HITL pause, not wait for the user to answer.

    Bug guarded: a login QR presented before ask_user would stay unsent
    until resume (or forever if the user never answers).
    """
    state = RenderState(bot_name="bot", run_id="run-pf", stream_interval=0.0)
    state.card_id = "card-1"
    dispatcher = _RecordingDispatcher()
    connector = _FakeConnector()
    flushes = {"presented": 0, "artifacts": 0}

    class _ArtDisp:
        async def handle(self, _artifact: Any) -> None:
            return None

        async def handle_presented(self, _presented: Any) -> None:
            return None

        async def deliver_terminal_files(self) -> None:
            flushes["artifacts"] += 1

        async def deliver_terminal_presented(self) -> None:
            flushes["presented"] += 1

    tailer = OutboundRunTailer(
        redis=AsyncMock(),
        key_prefix="cb-",
        run_id="run-pf",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        artifact_dispatcher=_ArtDisp(),
        block_ms=1,
    )

    events: list[_FakeEvent] = [
        _FakeEvent(
            "1",
            {"type": "presented_file", "data": {"presented_file": {"id": "pfile-1"}}},
        ),
        _FakeEvent("2", {"type": "done", "data": {"paused": True}}),
    ]

    async def _read(*_a: Any, **_k: Any) -> list[_FakeEvent]:
        nonlocal events
        batch, events = events, []
        if not batch:
            await asyncio.Event().wait()
        return batch

    import cubeplex.im.outbound as outbound_mod

    original = outbound_mod.read_run_events_after
    outbound_mod.read_run_events_after = _read  # type: ignore[assignment]
    task = asyncio.create_task(tailer.run())
    try:
        for _ in range(100):
            if flushes["presented"] == 1:
                break
            await asyncio.sleep(0.01)
        assert flushes["presented"] == 1
        assert flushes["artifacts"] == 0
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        outbound_mod.read_run_events_after = original  # type: ignore[assignment]
