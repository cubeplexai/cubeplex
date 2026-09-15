"""Subagent event bridge between cubeloop middleware and cubeplex SSE queues."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cubeloop.agent.types import MessageUpdateEvent
from cubeloop.providers.base import AssistantMessage, StreamEvent, TextContent

from cubeplex.streams.execution_adapter import EventProjectionError
from cubeplex.streams.subagent_events import (
    forward_subagent_event,
    map_subagent_event,
    subagent_event_queue,
)


def test_map_subagent_event_uses_cubeplex_sse_translation() -> None:
    event = MessageUpdateEvent(
        message=AssistantMessage(content=[TextContent(text="")]),
        stream_event=StreamEvent(type="text_delta", delta="hello"),
    )

    assert map_subagent_event(event) == [{"type": "text_delta", "delta": "hello"}]


@pytest.mark.asyncio
async def test_forward_subagent_event_tags_payload_and_queues_it() -> None:
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    payload: dict[str, Any] = {"type": "text_delta", "delta": "hello"}
    token = subagent_event_queue.set(queue)
    try:
        await forward_subagent_event("subagent:tc-1", payload)
    finally:
        subagent_event_queue.reset(token)

    assert payload["agent_id"] == "subagent:tc-1"
    assert queue.get_nowait() == ("subagent", "subagent:tc-1", payload)


@pytest.mark.asyncio
async def test_forward_subagent_event_without_queue_only_tags_payload() -> None:
    payload: dict[str, Any] = {"type": "text_delta", "delta": "hello"}

    await forward_subagent_event("subagent:tc-2", payload)

    assert payload["agent_id"] == "subagent:tc-2"


@pytest.mark.asyncio
async def test_forward_subagent_event_fails_when_queue_stays_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import subagent_events

    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue(maxsize=1)
    queue.put_nowait(("subagent", "existing", {}))

    async def enqueue_with_short_timeout(queue: Any, item: Any) -> None:
        from cubeplex.streams.execution_adapter import enqueue_host_event

        await enqueue_host_event(queue, item, timeout_seconds=0.01)

    monkeypatch.setattr(subagent_events, "enqueue_host_event", enqueue_with_short_timeout)
    token = subagent_event_queue.set(queue)
    try:
        with pytest.raises(EventProjectionError, match="timed out enqueueing"):
            await forward_subagent_event("subagent:tc-3", {"type": "text_delta"})
    finally:
        subagent_event_queue.reset(token)

    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_forward_subagent_event_propagates_unexpected_queue_error() -> None:
    class _BrokenQueue:
        async def put(self, _item: Any) -> None:
            raise RuntimeError("queue is broken")

    token = subagent_event_queue.set(_BrokenQueue())  # type: ignore[arg-type]
    try:
        with pytest.raises(RuntimeError, match="queue is broken"):
            await forward_subagent_event("subagent:tc-4", {"type": "text_delta"})
    finally:
        subagent_event_queue.reset(token)
