"""Unit tests for ``_drain_cubepi_sse_queue`` (PR #84 review).

Regression guard for the cubepi streaming fix: SSE dicts must flow through
``publish_stream_event`` as the agent emits them, not in a single batch after
``agent.prompt()`` returns.

The previous implementation collected dicts into a list and flushed them all
at the end; long model responses appeared as one batched dump to the client.
The fix bridges the synchronous cubepi listener to the async world via an
``asyncio.Queue`` plus a parallel drain task.  These tests cover the drainer
contract directly:

- Events queued with delays between them are published with comparable
  per-event delays (no batching).
- The ``None`` sentinel terminates the drainer cleanly.
- Each published event carries a fresh timestamp (not a single shared value).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from cubeplex.streams.run_manager import _drain_cubepi_sse_queue


@pytest.mark.asyncio
async def test_drainer_publishes_events_as_they_arrive() -> None:
    """Events queued with sleeps between them must be published incrementally."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    publish_times: list[float] = []

    async def fake_publish(sse_event: Any, _agent_key: Any) -> None:
        publish_times.append(time.monotonic())

    drainer = asyncio.create_task(_drain_cubepi_sse_queue(queue, fake_publish))

    async def producer() -> None:
        for i in range(3):
            queue.put_nowait({"type": "text_delta", "data": {"content": f"chunk-{i}"}})
            await asyncio.sleep(0.02)
        queue.put_nowait(None)

    await producer()
    await drainer

    # 3 events published.
    assert len(publish_times) == 3

    # The spread between first and last publish must exceed a single tick;
    # if the old "collect then flush" pattern were in place we'd see all
    # three timestamps clustered after producer() finishes.
    span = publish_times[-1] - publish_times[0]
    assert span >= 0.02, f"events appear batched (span={span:.4f}s)"

    # Each consecutive pair should also show measurable separation, not all
    # piled at the end.
    for prev, curr in zip(publish_times, publish_times[1:], strict=False):
        assert curr - prev >= 0.005, (
            f"consecutive publishes too close: {curr - prev:.4f}s; pattern indicates batching"
        )


@pytest.mark.asyncio
async def test_drainer_exits_on_none_sentinel() -> None:
    """Posting None terminates the drainer."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: Any) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_cubepi_sse_queue(queue, fake_publish))
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)
    assert published == []


@pytest.mark.asyncio
async def test_drainer_skips_unmappable_dicts() -> None:
    """Dicts ``cubepi_dict_to_agent_event`` can't translate are silently dropped."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: Any) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_cubepi_sse_queue(queue, fake_publish))
    queue.put_nowait({"type": "definitely_unknown_event_kind"})
    queue.put_nowait({"type": "text_delta", "data": {"content": "hi"}})
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    # Exactly one published event; the unknown type was skipped.
    assert len(published) == 1
    assert published[0].type == "text_delta"


@pytest.mark.asyncio
async def test_drainer_uses_fresh_timestamp_per_event() -> None:
    """Each event must carry its own publication timestamp, not a shared one."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    timestamps: list[str] = []

    async def fake_publish(sse_event: Any, _agent_key: Any) -> None:
        timestamps.append(sse_event.timestamp)

    drainer = asyncio.create_task(_drain_cubepi_sse_queue(queue, fake_publish))
    queue.put_nowait({"type": "text_delta", "data": {"content": "a"}})
    await asyncio.sleep(0.02)
    queue.put_nowait({"type": "text_delta", "data": {"content": "b"}})
    await asyncio.sleep(0.02)
    queue.put_nowait({"type": "text_delta", "data": {"content": "c"}})
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(timestamps) == 3
    # Timestamps are ISO strings; lexicographic ordering matches chronological.
    assert timestamps[0] < timestamps[1] < timestamps[2], timestamps
