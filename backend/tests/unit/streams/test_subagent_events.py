"""Subagent event bridge between cubepi middleware and cubebox SSE queues."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cubepi.agent.types import MessageUpdateEvent
from cubepi.providers.base import AssistantMessage, StreamEvent, TextContent

from cubebox.streams.subagent_events import (
    forward_subagent_event,
    map_subagent_event,
    subagent_event_queue,
)


def test_map_subagent_event_uses_cubebox_sse_translation() -> None:
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
async def test_forward_subagent_event_drops_when_queue_full() -> None:
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue(maxsize=1)
    queue.put_nowait(("subagent", "existing", {}))
    token = subagent_event_queue.set(queue)
    try:
        await forward_subagent_event("subagent:tc-3", {"type": "text_delta"})
    finally:
        subagent_event_queue.reset(token)

    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_forward_subagent_event_swallows_unexpected_queue_error() -> None:
    class _BrokenQueue:
        def put_nowait(self, _item: Any) -> None:
            raise RuntimeError("queue is broken")

    token = subagent_event_queue.set(_BrokenQueue())  # type: ignore[arg-type]
    try:
        await forward_subagent_event("subagent:tc-4", {"type": "text_delta"})
    finally:
        subagent_event_queue.reset(token)
