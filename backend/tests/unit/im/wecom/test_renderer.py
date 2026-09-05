from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from cubeplex.im.card_model import ArtifactItem, PendingInput
from cubeplex.im.types import RenderState
from cubeplex.im.wecom.renderer import WecomOpDispatcher


class _Gateway:
    def __init__(self) -> None:
        self.passive: list[tuple[str, dict[str, Any], bool, bool]] = []
        self.proactive: list[tuple[str, dict[str, Any]]] = []
        self.passive_results: list[dict[str, Any]] = []

    async def send_passive(
        self,
        req_id: str,
        body: dict[str, Any],
        *,
        final: bool,
        skip_if_pending: bool,
    ) -> dict[str, Any]:
        self.passive.append((req_id, body, final, skip_if_pending))
        if self.passive_results:
            return self.passive_results.pop(0)
        return {"errcode": 0, "errmsg": "ok"}

    async def send_proactive(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.proactive.append((chat_id, body))
        return {"errcode": 0, "errmsg": "ok"}


@dataclass
class _Connector:
    gateway: _Gateway
    chat_id: str = "chat-1"

    async def send_stream(
        self,
        *,
        stream_id: str,
        content: str,
        final: bool,
    ) -> dict[str, Any]:
        return await self.gateway.send_passive(
            "req-1",
            {
                "msgtype": "stream",
                "stream": {"id": stream_id, "content": content, "finish": final},
            },
            final=final,
            skip_if_pending=not final,
        )

    async def send_proactive_text(self, text: str) -> str | None:
        return await self.send_to_chat(self.chat_id, None, text)

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str | None:
        assert reply_to_id is None
        await self.gateway.send_proactive(
            chat_id,
            {"msgtype": "markdown", "markdown": {"content": text}},
        )
        return "sent"


def _state(*, reply_to_id: str | None = "req-1") -> RenderState:
    state = RenderState(bot_name="CubePlex", run_id="run-1")
    state.reply_to_id = reply_to_id
    return state


@pytest.mark.asyncio
async def test_stream_uses_one_id_and_cumulative_content() -> None:
    gateway = _Gateway()
    state = _state()
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=state,
        now=lambda: 10.0,
    )

    assert await dispatcher.dispatch_create(state)
    state.card_state.streaming_content = "Hello"
    assert await dispatcher.dispatch_stream(state, "Hello")
    state.card_state.streaming_content = "Hello world"
    assert await dispatcher.dispatch_finalize(state)

    streams = [call[1]["stream"] for call in gateway.passive]
    assert len({stream["id"] for stream in streams}) == 1
    assert streams[1]["content"] == "Hello"
    assert streams[2]["content"] == "Hello world"
    assert streams[0]["finish"] is False
    assert streams[2]["finish"] is True


@pytest.mark.asyncio
async def test_final_text_is_utf8_safe_and_contains_visible_context() -> None:
    gateway = _Gateway()
    state = _state()
    state.card_state.streaming_content = "界" * 8000
    state.card_state.post_hitl_content = "after"
    state.card_state.error = "failed"
    state.card_state.artifacts.append(
        ArtifactItem(id="a1", artifact_type="file", name="report", share_url="https://a")
    )
    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Choose one",
    )
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=state,
        now=lambda: 10.0,
    )

    await dispatcher.dispatch_finalize(state)

    content = gateway.passive[-1][1]["stream"]["content"]
    assert len(content.encode("utf-8")) <= 20_480
    assert content.endswith("[Truncated — view the full response in CubePlex]")


@pytest.mark.asyncio
async def test_final_text_surfaces_post_hitl_error_artifact_and_pending_input() -> None:
    gateway = _Gateway()
    state = _state()
    state.card_state.streaming_content = "Before approval"
    state.card_state.post_hitl_content = "After approval"
    state.card_state.error = "Tool failed"
    state.card_state.artifacts.append(
        ArtifactItem(
            id="a1",
            artifact_type="file",
            name="report.csv",
            share_url="https://cubeplex.example/report.csv",
        )
    )
    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Choose one",
    )
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=state,
        now=lambda: 10.0,
    )

    await dispatcher.dispatch_finalize(state)

    content = gateway.passive[-1][1]["stream"]["content"]
    assert "Before approval" in content
    assert "After approval" in content
    assert "⚠️ Tool failed" in content
    assert "[report.csv](https://cubeplex.example/report.csv)" in content
    assert "Choose one" in content
    assert "Continue in the CubePlex web UI." in content


@pytest.mark.asyncio
async def test_no_request_id_and_expired_stream_use_proactive_final() -> None:
    gateway = _Gateway()
    no_request = _state(reply_to_id=None)
    no_request.card_state.streaming_content = "scheduled result"
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=no_request,
        now=lambda: 10.0,
    )
    assert await dispatcher.dispatch_create(no_request)
    assert await dispatcher.dispatch_stream(no_request, "scheduled result")
    assert await dispatcher.dispatch_finalize(no_request)
    assert gateway.passive == []
    assert gateway.proactive[-1][1]["markdown"]["content"] == "scheduled result"

    gateway = _Gateway()
    expired = _state()
    expired.card_state.streaming_content = "old result"
    times = iter([0.0, 331.0])
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=expired,
        now=lambda: next(times),
    )
    await dispatcher.dispatch_create(expired)
    await dispatcher.dispatch_finalize(expired)
    assert len(gateway.passive) == 1
    assert gateway.proactive[-1][1]["markdown"]["content"] == "old result"


@pytest.mark.asyncio
async def test_expired_passive_error_falls_back_to_proactive_final() -> None:
    gateway = _Gateway()
    gateway.passive_results = [
        {"errcode": 0, "errmsg": "ok"},
        {"errcode": 846604, "errmsg": "callback expired"},
    ]
    state = _state()
    state.card_state.streaming_content = "complete"
    dispatcher = WecomOpDispatcher(
        connector=_Connector(gateway),
        state=state,
        now=lambda: 10.0,
    )
    await dispatcher.dispatch_create(state)
    await dispatcher.dispatch_finalize(state)
    assert gateway.proactive[-1][1]["markdown"]["content"] == "complete"
