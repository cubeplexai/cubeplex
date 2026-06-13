"""Integration test: tailer dispatches to fake CardKit + fake connector."""

from __future__ import annotations

from typing import Any

import pytest

from cubebox.im.outbound import OutboundOp, OutboundRunTailer
from cubebox.im.types import RenderState


class _FakeCardKit:
    def __init__(self) -> None:
        self.creates: list[dict[str, Any]] = []
        self.streams: list[tuple[str, str, str, int]] = []
        self.patches: list[tuple[str, dict[str, Any], int]] = []
        self.finalized: list[tuple[str, dict[str, Any], int]] = []
        self.next_card_id = "AAQA"

    async def create_entity(self, card_json: dict[str, Any]) -> str:
        self.creates.append(card_json)
        return self.next_card_id

    async def stream_text(
        self, *, card_id: str, element_id: str, content: str, sequence: int
    ) -> None:
        self.streams.append((card_id, element_id, content, sequence))

    async def patch_card(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> None:
        self.patches.append((card_id, card_json, sequence))

    async def finalize(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> bool:
        self.finalized.append((card_id, card_json, sequence))
        return True


class _FakeConnector:
    def __init__(self) -> None:
        self.init_calls: list[str] = []
        self.emergency_texts: list[str] = []
        self.start_called = 0
        self.complete_called = 0
        self.failed_called = 0

    async def on_processing_start(self, state: RenderState) -> None:
        self.start_called += 1

    async def on_processing_complete(self, state: RenderState) -> None:
        self.complete_called += 1

    async def on_processing_failed(self, state: RenderState) -> None:
        self.failed_called += 1

    async def send_card_init_message(self, card_id: str) -> str | None:
        self.init_calls.append(card_id)
        return "om_bot_message_1"

    async def _send_emergency_text(self, text: str) -> str | None:
        self.emergency_texts.append(text)
        return "om_emergency_1"


def _new_tailer(
    state: RenderState, cardkit: _FakeCardKit, connector: _FakeConnector
) -> OutboundRunTailer:
    return OutboundRunTailer(
        redis=None,
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        cardkit=cardkit,
    )


@pytest.mark.asyncio
async def test_dispatch_card_create_creates_entity_and_sends_init_message() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_state.streaming_content = "hello"
    cardkit = _FakeCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)

    delivered = await tailer._dispatch_op(OutboundOp(kind="card_create"), is_terminal=False)
    assert delivered is True
    assert state.card_id == "AAQA"
    assert state.bot_message_id == "om_bot_message_1"
    assert conn.init_calls == ["AAQA"]
    assert len(cardkit.creates) == 1


@pytest.mark.asyncio
async def test_dispatch_stream_text_uses_monotonic_sequence() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_id = "AAQA"
    state.card_state.streaming_content = "hello world"
    cardkit = _FakeCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)
    delivered = await tailer._dispatch_op(
        OutboundOp(kind="stream_text", element_id="streaming_content", text=" world"),
        is_terminal=False,
    )
    assert delivered is True
    assert cardkit.streams == [("AAQA", "streaming_content", " world", 0)]
    assert state.card_state.next_seq == 1


@pytest.mark.asyncio
async def test_dispatch_patch_card_sends_full_json() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_id = "AAQA"
    state.card_state.streaming_content = "x"
    cardkit = _FakeCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)
    delivered = await tailer._dispatch_op(OutboundOp(kind="patch_card"), is_terminal=False)
    assert delivered is True
    assert len(cardkit.patches) == 1
    sent_json = cardkit.patches[0][1]
    assert sent_json["schema"] == "2.0"


@pytest.mark.asyncio
async def test_dispatch_finalize_sets_streaming_mode_false() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_id = "AAQA"
    state.card_state.streaming_content = "done"
    state.card_state.finalized = True
    cardkit = _FakeCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)
    delivered = await tailer._dispatch_op(OutboundOp(kind="finalize", final=True), is_terminal=True)
    assert delivered is True
    assert len(cardkit.finalized) == 1
    sent_json = cardkit.finalized[0][1]
    assert sent_json["config"]["streaming_mode"] is False


@pytest.mark.asyncio
async def test_dispatch_card_create_failure_engages_emergency_text() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_state.streaming_content = "Partial answer text"

    class _BrokenCardKit(_FakeCardKit):
        async def create_entity(self, card_json: dict[str, Any]) -> str:
            raise RuntimeError("CardKit 500")

    cardkit = _BrokenCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)
    delivered = await tailer._dispatch_op(OutboundOp(kind="card_create"), is_terminal=False)
    assert delivered is False
    assert state.card_unavailable is True
    # The warning + the cached partial were both sent as emergency text.
    assert any("飞书富文本渲染暂时不可用" in t for t in conn.emergency_texts)
    assert any("Partial answer text" in t for t in conn.emergency_texts)


@pytest.mark.asyncio
async def test_dispatch_with_card_unavailable_no_ops() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    state.card_unavailable = True
    cardkit = _FakeCardKit()
    conn = _FakeConnector()
    tailer = _new_tailer(state, cardkit, conn)
    delivered = await tailer._dispatch_op(
        OutboundOp(kind="stream_text", text="x"), is_terminal=False
    )
    assert delivered is False
    assert cardkit.streams == []
