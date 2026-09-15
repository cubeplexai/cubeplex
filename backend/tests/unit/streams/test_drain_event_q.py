"""Unit tests for ``_drain_subagent_citation_queue`` (regression #1).

Regression background: when the langgraph dispatch branch was removed in
the cubeloop cleanup (M6.6), the ``while True: event_q.get()`` consumer
loop went with it. subagent and citation middleware kept
pushing tagged tuples onto ``event_q`` (set into both
``subagent_event_queue`` and ``citation_event_queue`` ContextVars by
``_execute_run``), but nothing drained them — so subagent live streaming
and citation live events were silently dropped. They only resurfaced
after a page reload via the cubeloop checkpointer's ``details`` round-trip.

This test pins the contract of the replacement drainer that consumes
3-tuple items from the shared event queue and forwards typed AgentEvents
through ``publish_stream_event``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cubeplex.agents.schemas import (
    CitationEvent,
    TextDeltaEvent,
    ToolResultEvent,
)
from cubeplex.streams.execution_adapter import EventProjectionError
from cubeplex.streams.run_manager import (
    RunManager,
    _drain_subagent_citation_queue,
    _finish_subagent_citation_queue,
)


@pytest.mark.asyncio
async def test_drainer_translates_subagent_item_with_agent_id() -> None:
    """('subagent', agent_id, sse_dict) → typed AgentEvent carrying agent_id."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[tuple[Any, str | None]] = []

    async def fake_publish(sse_event: Any, agent_key: str | None) -> None:
        published.append((sse_event, agent_key))

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    queue.put_nowait(
        (
            "subagent",
            "subagent:tc-1",
            {"type": "text_delta", "delta": "sub says hi", "agent_id": "subagent:tc-1"},
        )
    )
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(published) == 1
    event, agent_key = published[0]
    assert isinstance(event, TextDeltaEvent)
    assert event.data["content"] == "sub says hi"
    assert event.agent_id == "subagent:tc-1"
    assert agent_key == "subagent:tc-1"


@pytest.mark.asyncio
async def test_drainer_forwards_subagent_tool_result_with_details() -> None:
    """Subagent tool_result events round-trip details (so inner save_artifact
    details survive to the frontend live, not only after reload)."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: str | None) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    queue.put_nowait(
        (
            "subagent",
            "subagent:tc-9",
            {
                "type": "tool_result",
                "tool_call_id": "inner-tc",
                "name": "save_artifact",
                "result": '{"action":"created","artifact":{"id":"art-1"}}',
                "details": {"foo": "bar"},
                "is_error": False,
                "agent_id": "subagent:tc-9",
            },
        )
    )
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(published) == 1
    evt = published[0]
    assert isinstance(evt, ToolResultEvent)
    assert evt.agent_id == "subagent:tc-9"
    assert evt.data["details"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_drainer_wraps_citation_item_into_citation_event() -> None:
    """('citation', agent_id, citation_data_dict) → CitationEvent."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[tuple[Any, str | None]] = []

    async def fake_publish(sse_event: Any, agent_key: str | None) -> None:
        published.append((sse_event, agent_key))

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    citation_payload = {
        "citation_id": 7,
        "chunks": [{"chunk_index": 0, "content": "snippet"}],
        "metadata": {"source_type": "web", "url": "https://x"},
        "tool_call_id": "tc-w",
    }
    queue.put_nowait(("citation", None, citation_payload))
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(published) == 1
    event, agent_key = published[0]
    assert isinstance(event, CitationEvent)
    assert event.data == citation_payload
    assert event.agent_id is None
    assert agent_key is None


@pytest.mark.asyncio
async def test_drainer_exits_on_none_sentinel() -> None:
    """Posting None terminates the drainer cleanly with no publishes."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: str | None) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)
    assert published == []


@pytest.mark.asyncio
async def test_drainer_skips_unknown_kinds() -> None:
    """Items whose kind is neither 'subagent' nor 'citation' are silently dropped."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: str | None) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    queue.put_nowait(("unknown_kind", None, {"type": "text_delta", "delta": "x"}))
    queue.put_nowait(
        ("subagent", "subagent:tc-2", {"type": "text_delta", "delta": "ok", "agent_id": "x"})
    )
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(published) == 1
    assert isinstance(published[0], TextDeltaEvent)
    assert published[0].data["content"] == "ok"


@pytest.mark.asyncio
async def test_drainer_skips_subagent_dicts_with_unmappable_type() -> None:
    """Subagent SSE dicts that the cubeloop→AgentEvent translator can't map
    (e.g. tool_call_delta, done) are silently dropped, matching the main
    Session consumer's conversion behavior."""
    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
    published: list[Any] = []

    async def fake_publish(sse_event: Any, _agent_key: str | None) -> None:
        published.append(sse_event)

    drainer = asyncio.create_task(_drain_subagent_citation_queue(queue, fake_publish))
    queue.put_nowait(("subagent", "subagent:tc-3", {"type": "done"}))
    queue.put_nowait(
        ("subagent", "subagent:tc-3", {"type": "text_delta", "delta": "after", "agent_id": "x"})
    )
    queue.put_nowait(None)
    await asyncio.wait_for(drainer, timeout=1.0)

    assert len(published) == 1
    assert isinstance(published[0], TextDeltaEvent)
    assert published[0].data["content"] == "after"


@pytest.mark.asyncio
async def test_finish_queue_times_out_and_cancels_stopped_drainer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import run_manager

    queue: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue(maxsize=1)
    queue.put_nowait(("subagent", "blocked", {}))
    drainer = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(run_manager, "HOST_EVENT_ENQUEUE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        await _finish_subagent_citation_queue(queue, drainer)  # type: ignore[arg-type]

    assert drainer.cancelled()


@pytest.mark.asyncio
async def test_append_event_rejects_oversized_projected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import execution_adapter

    monkeypatch.setattr(execution_adapter, "MAX_EVENT_BYTES", 1)
    manager = RunManager.__new__(RunManager)  # type: ignore[call-arg]

    with pytest.raises(EventProjectionError, match="maximum projected event size"):
        await manager._append_event(
            "run-1",
            "conversation-1",
            TextDeltaEvent(timestamp="2026-09-15T00:00:00Z", data={"content": "large"}),
        )
