"""Unit tests for OneShotLLM — accumulates text_delta events into a string."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from cubepi import Model
from cubepi.providers.base import StreamEvent, TextContent, UserMessage

from cubebox.llm.oneshot import OneShotLLM


class _FakeStream:
    """Async-iterable stand-in for ``cubepi.MessageStream``."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[StreamEvent]:
        async def gen() -> AsyncIterator[StreamEvent]:
            for e in self._events:
                yield e

        return gen()


class _FakeProvider:
    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        model: Model,
        messages: list,
        *,
        system_prompt: str = "",
        tools: Any = None,
        options: Any = None,
    ) -> _FakeStream:
        self.calls.append({"model": model, "messages": messages, "system_prompt": system_prompt})
        return _FakeStream(self._events)


@pytest.mark.asyncio
async def test_accumulates_text_deltas() -> None:
    events = [
        StreamEvent(type="text_delta", delta="hello "),
        StreamEvent(type="text_delta", delta="world"),
        StreamEvent(type="done"),
    ]
    provider = _FakeProvider(events)
    base_model = Model(id="m1", provider="p1", max_tokens=8192)

    llm = OneShotLLM(provider, base_model)
    result = await llm.generate_once(
        system="sys",
        messages=[UserMessage(content=[TextContent(text="hi")])],
        max_output_tokens=128,
    )

    assert result == "hello world"
    # max_tokens override propagated to the model passed to stream
    assert provider.calls[0]["model"].max_tokens == 128
    assert provider.calls[0]["system_prompt"] == "sys"


@pytest.mark.asyncio
async def test_error_event_raises() -> None:
    events = [
        StreamEvent(type="text_delta", delta="partial"),
        StreamEvent(type="error", error_message="provider exploded"),
    ]
    provider = _FakeProvider(events)
    base_model = Model(id="m1", provider="p1")

    llm = OneShotLLM(provider, base_model)

    with pytest.raises(RuntimeError, match="provider exploded"):
        await llm.generate_once(
            system="",
            messages=[UserMessage(content=[TextContent(text="hi")])],
            max_output_tokens=64,
        )
