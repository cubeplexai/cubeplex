"""Tests for register_awaiting_responder helper + tailer wiring."""

from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher


@pytest.mark.asyncio
async def test_register_awaiting_responder_writes_key_with_ttl() -> None:
    from cubeplex.im.outbound import register_awaiting_responder

    state: dict[str, tuple[str, int]] = {}

    async def fake_set(key: str, value: str, *, ex: int) -> None:
        state[key] = (value, ex)

    await register_awaiting_responder(
        run_id="run_1",
        responder_open_id="ou_user_1",
        redis_key_prefix="cubeplex-dev",
        set_fn=fake_set,
    )
    # Keys are prefixed so two envs sharing Redis don't collide.
    assert state["cubeplex-dev:run:run_1:awaiting_responder"] == ("ou_user_1", 600)


@pytest.mark.asyncio
async def test_register_awaiting_responder_noop_when_missing_inputs() -> None:
    from cubeplex.im.outbound import register_awaiting_responder

    state: dict[str, tuple[str, int]] = {}

    async def fake_set(key: str, value: str, *, ex: int) -> None:
        state[key] = (value, ex)

    # No responder_open_id → no write.
    await register_awaiting_responder(
        run_id="run_1",
        responder_open_id="",
        redis_key_prefix="cubeplex-dev",
        set_fn=fake_set,
    )
    assert state == {}


@pytest.mark.asyncio
async def test_tailer_registers_responder_after_ask_user_request() -> None:
    """Integration: tailer with a responder_open_id writes the binding
    when fold_event emits a pending_input op for ask_user_request."""
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.types import RenderState

    redis_writes: dict[str, tuple[str, int]] = {}

    class _FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            redis_writes[key] = (value, ex)

    class _FakeCardKit:
        async def create_entity(self, _: Any) -> str:
            return "AAQA"

        async def stream_text(self, **_: Any) -> None: ...

        async def patch_card(self, **_: Any) -> None: ...

        async def finalize(self, **_: Any) -> bool:
            return True

    class _FakeConnector:
        async def on_processing_start(self, _: RenderState) -> None: ...
        async def on_processing_complete(self, _: RenderState) -> None: ...
        async def on_processing_failed(self, _: RenderState) -> None: ...
        async def send_card_init_message(self, _: str) -> str | None:
            return "om_x"

        async def _send_emergency_text(self, _: str) -> str | None:
            return None

    state = RenderState(bot_name="cubeplex", run_id="run_1")
    connector = _FakeConnector()
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=_FakeCardKit())
    tailer = OutboundRunTailer(
        redis=_FakeRedis(),
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        responder_open_id="ou_user_42",
    )

    # The tailer's run() loop is hard to drive here without a real Redis
    # stream; instead, exercise the public `register_awaiting_responder_for_event`
    # helper directly with a representative event payload.
    await tailer.maybe_register_awaiting_responder(
        ev_payload={"type": "ask_user_request", "data": {"question_id": "q_1"}}
    )
    assert redis_writes["cb-:run:run_1:awaiting_responder"] == ("ou_user_42", 600)


@pytest.mark.asyncio
async def test_tailer_does_not_register_for_non_pending_events() -> None:
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.types import RenderState

    redis_writes: dict[str, tuple[str, int]] = {}

    class _FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            redis_writes[key] = (value, ex)

    class _FakeCardKit:
        async def create_entity(self, _: Any) -> str:
            return "AAQA"

        async def stream_text(self, **_: Any) -> None: ...
        async def patch_card(self, **_: Any) -> None: ...
        async def finalize(self, **_: Any) -> bool:
            return True

    class _FakeConnector:
        async def on_processing_start(self, _: RenderState) -> None: ...
        async def on_processing_complete(self, _: RenderState) -> None: ...
        async def on_processing_failed(self, _: RenderState) -> None: ...
        async def send_card_init_message(self, _: str) -> str | None:
            return "om_x"

        async def _send_emergency_text(self, _: str) -> str | None:
            return None

    state = RenderState(bot_name="cubeplex", run_id="run_1")
    connector = _FakeConnector()
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=_FakeCardKit())
    tailer = OutboundRunTailer(
        redis=_FakeRedis(),
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        responder_open_id="ou_user_42",
    )
    await tailer.maybe_register_awaiting_responder(
        ev_payload={"type": "text_delta", "data": {"content": "hi"}}
    )
    assert redis_writes == {}


@pytest.mark.asyncio
async def test_tailer_registers_for_sandbox_confirm_request() -> None:
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.types import RenderState

    redis_writes: dict[str, tuple[str, int]] = {}

    class _FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            redis_writes[key] = (value, ex)

    class _FakeCardKit:
        async def create_entity(self, _: Any) -> str:
            return "AAQA"

        async def stream_text(self, **_: Any) -> None: ...
        async def patch_card(self, **_: Any) -> None: ...
        async def finalize(self, **_: Any) -> bool:
            return True

    class _FakeConnector:
        async def on_processing_start(self, _: RenderState) -> None: ...
        async def on_processing_complete(self, _: RenderState) -> None: ...
        async def on_processing_failed(self, _: RenderState) -> None: ...
        async def send_card_init_message(self, _: str) -> str | None:
            return "om_x"

        async def _send_emergency_text(self, _: str) -> str | None:
            return None

    state = RenderState(bot_name="cubeplex", run_id="run_1")
    connector = _FakeConnector()
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=_FakeCardKit())
    tailer = OutboundRunTailer(
        redis=_FakeRedis(),
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        responder_open_id="ou_user_X",
    )
    await tailer.maybe_register_awaiting_responder(
        ev_payload={"type": "sandbox_confirm_request", "data": {"question_id": "qsc_1"}}
    )
    assert redis_writes["cb-:run:run_1:awaiting_responder"] == ("ou_user_X", 600)


@pytest.mark.asyncio
async def test_tailer_uses_event_timeout_seconds_for_responder_ttl() -> None:
    """When the cubepi event carries ``timeout_seconds``, the responder
    binding TTL must use it — otherwise a 30-minute HITL pause expires the
    binding after 10 minutes and a still-valid click surfaces "这不是发给你的".
    """
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.types import RenderState

    redis_writes: dict[str, tuple[str, int]] = {}

    class _FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            redis_writes[key] = (value, ex)

    class _FakeCardKit:
        async def create_entity(self, _: Any) -> str:
            return "AAQA"

        async def stream_text(self, **_: Any) -> None: ...
        async def patch_card(self, **_: Any) -> None: ...
        async def finalize(self, **_: Any) -> bool:
            return True

    class _FakeConnector:
        async def on_processing_start(self, _: RenderState) -> None: ...
        async def on_processing_complete(self, _: RenderState) -> None: ...
        async def on_processing_failed(self, _: RenderState) -> None: ...
        async def send_card_init_message(self, _: str) -> str | None:
            return "om_x"

        async def _send_emergency_text(self, _: str) -> str | None:
            return None

    state = RenderState(bot_name="cubeplex", run_id="run_30m")
    connector = _FakeConnector()
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=_FakeCardKit())
    tailer = OutboundRunTailer(
        redis=_FakeRedis(),
        key_prefix="cb-",
        run_id="run_30m",
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        responder_open_id="ou_user_Y",
    )
    await tailer.maybe_register_awaiting_responder(
        ev_payload={
            "type": "ask_user_request",
            "data": {"question_id": "q_long", "timeout_seconds": 1800},
        }
    )
    assert redis_writes["cb-:run:run_30m:awaiting_responder"] == ("ou_user_Y", 1800)


@pytest.mark.asyncio
async def test_register_awaiting_responder_clamps_ttl_max() -> None:
    """A malformed event with a huge timeout must clamp at the 24h cap."""
    from cubeplex.im.outbound import register_awaiting_responder

    state: dict[str, tuple[str, int]] = {}

    async def fake_set(key: str, value: str, *, ex: int) -> None:
        state[key] = (value, ex)

    await register_awaiting_responder(
        run_id="run_huge",
        responder_open_id="ou_x",
        redis_key_prefix="cb-",
        set_fn=fake_set,
        ttl_seconds=10**9,
    )
    assert state["cb-:run:run_huge:awaiting_responder"] == ("ou_x", 24 * 60 * 60)
