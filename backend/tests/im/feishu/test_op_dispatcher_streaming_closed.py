"""FeishuOpDispatcher: 300309 streaming-closed → patch fallback + quiet logs."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cubeplex.im.feishu.cardkit_client import CardKitStreamingClosed
from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher
from cubeplex.im.types import RenderState


class _FakeCardKit:
    def __init__(self) -> None:
        self.stream_calls = 0
        self.patch_calls = 0
        self.finalize_calls = 0
        self.fail_stream_with: Exception | None = CardKitStreamingClosed(
            "stream_text code=300309 msg=ErrMsg: streaming mode is closed; "
        )

    async def stream_text(self, **_: Any) -> None:
        self.stream_calls += 1
        if self.fail_stream_with is not None:
            raise self.fail_stream_with

    async def patch_card(self, **_: Any) -> None:
        self.patch_calls += 1

    async def finalize(self, **_: Any) -> bool:
        self.finalize_calls += 1
        return True

    async def create_entity(self, *_: Any, **__: Any) -> str:
        return "card-1"

    async def aclose(self) -> None:
        return None


def _state(*, card_id: str = "card-1") -> RenderState:
    state = RenderState(bot_name="bot", run_id="run-1")
    state.card_id = card_id
    state.card_state.streaming_content = "hello world"
    # Make throttle easy to force/skip in tests.
    state.patch_interval = 1.5
    state.last_patch_monotonic = 0.0
    return state


def _dispatcher(cardkit: _FakeCardKit, state: RenderState) -> FeishuOpDispatcher:
    connector = AsyncMock()
    return FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)


@pytest.mark.asyncio
async def test_first_300309_falls_back_to_patch_and_sets_flag() -> None:
    cardkit = _FakeCardKit()
    state = _state()
    d = _dispatcher(cardkit, state)

    ok = await d.dispatch_stream(state, "hello world")

    assert ok is True
    assert state.streaming_closed is True
    assert state.stream_closed_skip_count == 1
    assert cardkit.stream_calls == 1
    assert cardkit.patch_calls == 1


@pytest.mark.asyncio
async def test_subsequent_stream_skips_stream_text_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cardkit = _FakeCardKit()
    state = _state()
    state.streaming_closed = True
    state.stream_closed_skip_count = 1
    # First redirected patch should go through (last_patch is 0).
    d = _dispatcher(cardkit, state)

    ok1 = await d.dispatch_stream(state, "hello world")
    assert ok1 is True
    assert cardkit.stream_calls == 0
    assert cardkit.patch_calls == 1
    assert state.stream_closed_skip_count == 2

    # Immediate second stream: throttled, still counted, no extra patch.
    ok2 = await d.dispatch_stream(state, "hello world more")
    assert ok2 is False
    assert cardkit.stream_calls == 0
    assert cardkit.patch_calls == 1
    assert state.stream_closed_skip_count == 3

    # Advance past patch_interval → another patch.
    monkeypatch.setattr(
        "cubeplex.im.feishu.op_dispatcher.time.monotonic",
        lambda: state.last_patch_monotonic + state.patch_interval + 0.01,
    )
    ok3 = await d.dispatch_stream(state, "hello world more still")
    assert ok3 is True
    assert cardkit.patch_calls == 2
    assert state.stream_closed_skip_count == 4


@pytest.mark.asyncio
async def test_300309_logs_warning_once(monkeypatch: pytest.MonkeyPatch) -> None:
    cardkit = _FakeCardKit()
    state = _state()
    d = _dispatcher(cardkit, state)
    warnings: list[str] = []

    def _capture(msg: str, *args: Any, **__: Any) -> None:
        warnings.append(msg.format(*args) if args else msg)

    monkeypatch.setattr(
        "cubeplex.im.feishu.op_dispatcher.logger.warning",
        _capture,
    )

    await d.dispatch_stream(state, "a")
    # Already closed — must not warn again.
    await d.dispatch_stream(state, "ab")
    await d.dispatch_stream(state, "abc")

    closed_warnings = [w for w in warnings if "300309" in w or "streaming mode closed" in w]
    assert len(closed_warnings) == 1
    assert state.stream_closed_skip_count == 3


@pytest.mark.asyncio
async def test_finalize_still_works_after_streaming_closed() -> None:
    cardkit = _FakeCardKit()
    state = _state()
    state.streaming_closed = True
    state.stream_closed_skip_count = 12
    state.card_state.finalized = True
    d = _dispatcher(cardkit, state)

    ok = await d.dispatch_finalize(state)
    assert ok is True
    assert cardkit.finalize_calls == 1


@pytest.mark.asyncio
async def test_other_stream_errors_still_log_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cardkit = _FakeCardKit()
    cardkit.fail_stream_with = RuntimeError("boom")
    state = _state()
    d = _dispatcher(cardkit, state)

    opt_calls: list[bool] = []

    class _Opt:
        def warning(self, *_: Any, **__: Any) -> None:
            return None

    def _opt(*, exception: bool = False) -> _Opt:
        opt_calls.append(exception)
        return _Opt()

    monkeypatch.setattr(
        "cubeplex.im.feishu.op_dispatcher.logger.opt",
        _opt,
    )

    ok = await d.dispatch_stream(state, "x")
    assert ok is False
    assert state.streaming_closed is False
    assert cardkit.patch_calls == 0
    assert opt_calls == [True]
