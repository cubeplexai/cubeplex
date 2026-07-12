# Reconnect Replay Coalescing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the browser freezing when the page is refreshed during a busy agent run, by folding the replayed event backlog server-side and yielding to the event loop in the frontend consume loops.

**Architecture:** On reconnect the backend currently replays every fine-grained SSE event (tens of thousands of 1-character `text_delta` / `tool_call_delta`) from the Redis stream, which the frontend folds O(N²) on the main thread. We add a stateful `ReplayCoalescer` that merges adjacent same-key deltas in strict stream order, read the backlog in bounded chunks (so the asyncio loop never stalls), and add a periodic yield in the two frontend consume loops as a safety net. Live tail stays fine-grained for smooth streaming. No cubepi changes, no checkpoint correlation.

**Tech Stack:** Python (FastAPI, redis.asyncio, pytest), TypeScript (Zustand store, Vitest).

**Spec:** `docs/dev/specs/2026-05-26-reconnect-replay-coalescing-design.md`

---

## File Structure

- **Create** `backend/cubeplex/streams/replay_coalescer.py` — the pure, stateful `ReplayCoalescer` (no IO). One responsibility: fold a stream of `RunEvent` into a compact, order-preserving equivalent.
- **Create** `backend/tests/unit/test_replay_coalescer.py` — unit tests for the coalescer.
- **Modify** `backend/cubeplex/streams/run_events.py` — add `iter_run_events_chunked` (cursor-paginated batch reader). Leave `iter_run_events` and its callers untouched.
- **Create** `backend/tests/unit/test_iter_run_events_chunked.py` — test for the chunked reader. Uses an inline `fakeredis` fixture with `decode_responses=True` (matches production `app.py:141` and the convention in `tests/unit/test_run_control_pubsub.py`).
- **Modify** `backend/cubeplex/api/routes/v1/conversations.py` — replay segment of `event_generator` uses the chunked reader + coalescer.
- **Modify** `frontend/packages/core/src/stores/messageStore.ts` — periodic yield in both consume loops (`consumeRunStream`, `send`).
- **Create** `frontend/packages/web/__tests__/stores/messageStore.yield.test.ts` — asserts the yield fires for large event counts.

---

## Task 1: ReplayCoalescer (pure, streaming)

**Files:**
- Create: `backend/cubeplex/streams/replay_coalescer.py`
- Test: `backend/tests/unit/test_replay_coalescer.py`

The coalescer holds **at most one** pending mergeable run at a time. When an event arrives whose key differs from the pending run's key (or is non-mergeable), the pending run is flushed first — this is what preserves SSE order and event-id dedup monotonicity on the frontend.

Merge keys:
- `text_delta` → `("text_delta", agent_id)`
- `reasoning` → `("reasoning", agent_id)`
- `tool_call_delta` → `("tool_call_delta", agent_id, index)`

Every other event type is non-mergeable (pass-through). A coalesced event keeps the `event_id` of the **last** original event it merged.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_replay_coalescer.py`:

```python
from cubeplex.streams.replay_coalescer import ReplayCoalescer
from cubeplex.streams.run_events import RunEvent


def _ev(event_id: str, etype: str, *, data=None, agent_id=None) -> RunEvent:
    return RunEvent(
        event_id=event_id,
        payload={
            "type": etype,
            "timestamp": "",
            "data": data or {},
            "agent_id": agent_id,
            "agent_name": None,
        },
    )


def _run(events):
    c = ReplayCoalescer()
    out = c.feed(events)
    out += c.flush()
    return out


def test_consecutive_text_delta_same_agent_merges():
    out = _run([
        _ev("1-0", "text_delta", data={"content": "Hel"}),
        _ev("2-0", "text_delta", data={"content": "lo"}),
    ])
    assert len(out) == 1
    assert out[0].payload["data"]["content"] == "Hello"
    assert out[0].event_id == "2-0"  # last merged id


def test_consecutive_reasoning_same_agent_merges():
    out = _run([
        _ev("1-0", "reasoning", data={"content": "th"}),
        _ev("2-0", "reasoning", data={"content": "ink"}),
    ])
    assert len(out) == 1
    assert out[0].payload["data"]["content"] == "think"


def test_tool_call_delta_merges_per_index_keeps_type():
    out = _run([
        _ev("1-0", "tool_call_delta", data={"index": 0, "args_delta": '{"a"', "name": "calc", "tool_call_id": "t1"}),
        _ev("2-0", "tool_call_delta", data={"index": 0, "args_delta": ":1}", "name": None, "tool_call_id": None}),
    ])
    assert len(out) == 1
    assert out[0].payload["type"] == "tool_call_delta"
    assert out[0].payload["data"]["args_delta"] == '{"a":1}'
    assert out[0].payload["data"]["tool_call_id"] == "t1"
    assert out[0].payload["data"]["name"] == "calc"


def test_structural_events_pass_through():
    events = [
        _ev("1-0", "tool_call", data={"tool_call_id": "t1", "name": "calc", "arguments": {}}),
        _ev("2-0", "tool_result", data={"tool_call_id": "t1", "content": "1"}),
        _ev("3-0", "usage", data={"input_tokens": 1}),
        _ev("4-0", "citation", data={}),
        _ev("5-0", "artifact", data={}),
        _ev("6-0", "injected_message", data={"content": "x", "steer_id": "s"}),
        _ev("7-0", "status", data={"phase": "x"}),
    ]
    out = _run(events)
    assert [e.event_id for e in out] == ["1-0", "2-0", "3-0", "4-0", "5-0", "6-0", "7-0"]


def test_interleave_preserves_stream_order():
    # main e1, subagent e2, main e3 -> e1, e2, e3 (never e1+e3 around e2)
    out = _run([
        _ev("1-0", "text_delta", data={"content": "A"}, agent_id=None),
        _ev("2-0", "text_delta", data={"content": "B"}, agent_id="subagent:t1"),
        _ev("3-0", "text_delta", data={"content": "C"}, agent_id=None),
    ])
    assert [e.event_id for e in out] == ["1-0", "2-0", "3-0"]
    assert out[0].payload["data"]["content"] == "A"
    assert out[1].payload["data"]["content"] == "B"
    assert out[2].payload["data"]["content"] == "C"


def test_text_interrupted_by_tool_call_splits():
    out = _run([
        _ev("1-0", "text_delta", data={"content": "before"}),
        _ev("2-0", "tool_call", data={"tool_call_id": "t1", "name": "calc", "arguments": {}}),
        _ev("3-0", "text_delta", data={"content": "after"}),
    ])
    assert [e.payload["type"] for e in out] == ["text_delta", "tool_call", "text_delta"]
    assert out[0].payload["data"]["content"] == "before"
    assert out[2].payload["data"]["content"] == "after"


def test_done_flushes_pending_then_passes_through():
    out = _run([
        _ev("1-0", "text_delta", data={"content": "hi"}),
        _ev("2-0", "done", data={}),
    ])
    assert [e.payload["type"] for e in out] == ["text_delta", "done"]
    assert out[0].payload["data"]["content"] == "hi"


def test_empty_input():
    assert _run([]) == []


def test_chunk_boundary_invariance():
    events = [
        _ev("1-0", "text_delta", data={"content": "A"}),
        _ev("2-0", "text_delta", data={"content": "B"}),
        _ev("3-0", "tool_call_delta", data={"index": 0, "args_delta": "{", "name": "c", "tool_call_id": "t"}),
        _ev("4-0", "tool_call_delta", data={"index": 0, "args_delta": "}", "name": None, "tool_call_id": None}),
        _ev("5-0", "done", data={}),
    ]
    whole = _run(events)
    # Feed split at every boundary; output must be identical.
    for split in range(len(events) + 1):
        c = ReplayCoalescer()
        chunked = c.feed(events[:split]) + c.feed(events[split:]) + c.flush()
        assert [(e.event_id, e.payload["type"], e.payload["data"]) for e in chunked] == \
               [(e.event_id, e.payload["type"], e.payload["data"]) for e in whole]


def test_size_cap_splits_huge_run_into_bounded_events():
    # 6 deltas of 4 chars (24 total) with a 10-char cap -> multiple bounded
    # text_delta events whose contents concatenate back to the original.
    c = ReplayCoalescer(max_chars=10)
    events = [_ev(f"{i}-0", "text_delta", data={"content": "abcd"}) for i in range(6)]
    out = c.feed(events) + c.flush()
    assert len(out) >= 2
    assert all(e.payload["type"] == "text_delta" for e in out)
    assert "".join(e.payload["data"]["content"] for e in out) == "abcd" * 6
    # No emitted chunk grossly exceeds the cap (cap + at most one delta).
    assert all(len(e.payload["data"]["content"]) <= 10 + 4 for e in out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_replay_coalescer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.streams.replay_coalescer'`

- [ ] **Step 3: Implement the coalescer**

Create `backend/cubeplex/streams/replay_coalescer.py`:

```python
"""Fold a run's replayed event backlog into a compact, order-preserving set.

On reconnect the backend replays the whole Redis event stream for the active
run. A long run emits tens of thousands of 1-character ``text_delta`` /
``tool_call_delta`` events, which the frontend folds O(N²) on the main thread
— the cause of the freeze-on-reload. This coalescer merges *adjacent*
same-key deltas so the replayed payload shrinks to roughly message
granularity, while preserving SSE order (the frontend applies replayed events
in order and dedups by ``event_id``).

Two performance invariants:
- Accumulation is O(N): delta pieces go into a list and are ``"".join()``ed
  once when the run is emitted — never ``s = s + delta`` per event (that
  copies the whole growing string each time, re-introducing the backend stall).
- A run is also flushed when it reaches ``MAX_COALESCED_CHARS``, so one
  enormous message becomes several bounded ``text_delta`` events. This keeps
  each join + each frontend reducer/render step bounded and lets the
  frontend's count-based yield fire (a single huge event would slip it).

Streaming and stateful so it can fold across read chunks without holding the
whole backlog in memory. Pure (no IO).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from cubeplex.streams.run_events import RunEvent

# Max accumulated characters in one coalesced delta before it is flushed and a
# fresh same-key run continues. ~64 KiB: large enough that normal messages
# stay a single event, small enough that each join/render step is cheap.
MAX_COALESCED_CHARS = 65536

# Mergeable event type -> the data field whose value accumulates.
_DELTA_FIELD = {
    "text_delta": "content",
    "reasoning": "content",
    "tool_call_delta": "args_delta",
}


def _merge_key(event: RunEvent) -> tuple[Any, ...] | None:
    """Coalescing key for a mergeable event, or None when not mergeable."""
    payload = event.payload
    etype = payload.get("type")
    if etype in ("text_delta", "reasoning"):
        return (etype, payload.get("agent_id"))
    if etype == "tool_call_delta":
        data = payload.get("data") or {}
        return (etype, payload.get("agent_id"), data.get("index"))
    return None


@dataclass(slots=True)
class _Pending:
    """An in-progress coalesced run. ``pieces`` are joined once at emit time."""

    key: tuple[Any, ...]
    payload: dict[str, Any]  # mutable copy of the first event's payload
    field: str  # "content" | "args_delta"
    pieces: list[str] = field(default_factory=list)
    size: int = 0
    last_event_id: str = ""


class ReplayCoalescer:
    """Folds adjacent same-key deltas; flushes on key change or size cap.

    Holds at most one pending mergeable run. ``feed`` returns the events that
    are now complete (the pending run is held back until a differing event, or
    the size cap, forces a flush); ``flush`` emits whatever remains at end of
    replay.
    """

    def __init__(self, max_chars: int = MAX_COALESCED_CHARS) -> None:
        self._max_chars = max_chars
        self._pending: _Pending | None = None

    def feed(self, events: list[RunEvent]) -> list[RunEvent]:
        out: list[RunEvent] = []
        for event in events:
            key = _merge_key(event)
            if self._pending is not None and key == self._pending.key:
                self._absorb(event)
                if self._pending.size >= self._max_chars:
                    out.append(self._finish())  # emit a bounded chunk
                continue
            # Different key (or non-mergeable): flush the pending run first so
            # nothing is emitted out of stream order.
            if self._pending is not None:
                out.append(self._finish())
            if key is not None:
                self._start(event, key)
            else:
                out.append(event)
        return out

    def flush(self) -> list[RunEvent]:
        return [self._finish()] if self._pending is not None else []

    def _start(self, event: RunEvent, key: tuple[Any, ...]) -> None:
        payload = deepcopy(event.payload)
        fld = _DELTA_FIELD[payload["type"]]
        data = payload.setdefault("data", {})
        piece = data.get(fld) or ""
        self._pending = _Pending(
            key=key,
            payload=payload,
            field=fld,
            pieces=[piece],
            size=len(piece),
            last_event_id=event.event_id,
        )

    def _absorb(self, event: RunEvent) -> None:
        assert self._pending is not None
        edata = event.payload.get("data") or {}
        piece = edata.get(self._pending.field) or ""
        self._pending.pieces.append(piece)
        self._pending.size += len(piece)
        self._pending.last_event_id = event.event_id
        if self._pending.payload["type"] == "tool_call_delta":
            # Latest non-None identity wins (mirrors the route-level backfill).
            pdata = self._pending.payload["data"]
            if edata.get("tool_call_id") is not None:
                pdata["tool_call_id"] = edata["tool_call_id"]
            if edata.get("name") is not None:
                pdata["name"] = edata["name"]

    def _finish(self) -> RunEvent:
        assert self._pending is not None
        p = self._pending
        p.payload["data"][p.field] = "".join(p.pieces)
        # Stamp the last merged original id so frontend dedup stays monotonic.
        result = RunEvent(p.last_event_id, p.payload)
        self._pending = None
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_replay_coalescer.py -v`
Expected: PASS (all 10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/streams/replay_coalescer.py backend/tests/unit/test_replay_coalescer.py
git commit -m "feat(streams): add ReplayCoalescer for reconnect replay folding"
```

---

## Task 2: Chunked run-event reader

**Files:**
- Modify: `backend/cubeplex/streams/run_events.py` (add `iter_run_events_chunked`; leave `iter_run_events` untouched)
- Test: `backend/tests/unit/test_iter_run_events_chunked.py`

Reads `[start, stop]` in batches of ~`count` using a `(<id>` exclusive cursor for pagination, so each `await xrange` returns control to the event loop and memory stays bounded to one batch. `start` may be `None` (read from the beginning), a bare id, or an exclusive `(<id>` form (as the route passes when a `Last-Event-ID` header is present).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_iter_run_events_chunked.py`. It seeds the
stream directly via `xadd` (no `append_run_event` Lua dependency) and reads
through the real `iter_run_events_chunked`. The fixture uses
`decode_responses=True` so `_decode_stream_entries` (which indexes the `str`
key `"payload"`) works exactly as in production:

```python
import json

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_events import (
    _run_events_key,
    iter_run_events_chunked,
)

PREFIX = "test-chunked"


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def _seed(redis, run_id: str, n: int) -> list[str]:
    key = _run_events_key(PREFIX, run_id)
    ids = []
    for i in range(n):
        payload = json.dumps(
            {"type": "text_delta", "timestamp": "", "data": {"content": str(i)},
             "agent_id": None, "agent_name": None}
        )
        ids.append(await redis.xadd(key, {"payload": payload}))
    return ids


@pytest.mark.asyncio
async def test_chunked_reads_all_events_in_order(redis):
    ids = await _seed(redis, "run-chunk-1", 25)

    batches = []
    async for batch in iter_run_events_chunked(
        redis, prefix=PREFIX, run_id="run-chunk-1", start=None, stop="+", count=10
    ):
        batches.append(batch)

    # 25 events / count 10 -> 3 batches (10, 10, 5)
    assert [len(b) for b in batches] == [10, 10, 5]
    flat = [e.event_id for b in batches for e in b]
    assert flat == ids
    # Payload is decoded into a dict, content preserved in order.
    assert [e.payload["data"]["content"] for b in batches for e in b] == [str(i) for i in range(25)]


@pytest.mark.asyncio
async def test_chunked_honors_exclusive_start(redis):
    ids = await _seed(redis, "run-chunk-2", 5)

    flat = []
    async for batch in iter_run_events_chunked(
        redis, prefix=PREFIX, run_id="run-chunk-2", start=f"({ids[1]}", stop="+", count=10
    ):
        flat.extend(e.event_id for e in batch)

    # Exclusive of ids[1] -> starts at ids[2].
    assert flat == ids[2:]


@pytest.mark.asyncio
async def test_chunked_empty_stream(redis):
    batches = [
        b
        async for b in iter_run_events_chunked(
            redis, prefix=PREFIX, run_id="run-none", start=None, stop="+", count=10
        )
    ]
    assert batches == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_iter_run_events_chunked.py -v`
Expected: FAIL with `ImportError: cannot import name 'iter_run_events_chunked'`

- [ ] **Step 3: Implement the chunked reader**

In `backend/cubeplex/streams/run_events.py`, add the import at the top of the file (with the other typing imports) if not present:

```python
from collections.abc import AsyncIterator
```

Then add this function directly after `iter_run_events`:

```python
async def iter_run_events_chunked(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    start: str | None = None,
    stop: str | None = None,
    count: int = 1000,
) -> AsyncIterator[list[RunEvent]]:
    """Yield decoded run events in ``[start, stop]`` in batches of ~``count``.

    Paginates with ``XRANGE`` using a ``(<id>`` exclusive cursor between pages,
    so each ``await`` hands control back to the event loop and memory stays
    bounded to one batch. ``start`` may be None (from the beginning), a bare
    id, or an already-exclusive ``(<id>`` form. ``stop`` is inclusive.
    """
    stream_key = _run_events_key(prefix, run_id)
    min_id = start if start is not None else "-"
    max_id = stop if stop is not None else "+"
    while True:
        entries = await redis.xrange(stream_key, min=min_id, max=max_id, count=count)
        if not entries:
            return
        yield _decode_stream_entries(entries)
        if len(entries) < count:
            return
        min_id = f"({entries[-1][0]}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_iter_run_events_chunked.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/streams/run_events.py backend/tests/unit/test_iter_run_events_chunked.py
git commit -m "feat(streams): add chunked run-event reader for bounded replay"
```

---

## Task 3: Wire coalescer + chunked reader into the stream route

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py` (replay segment of `event_generator`, around lines 321-340)

The replay segment is replaced so it reads in chunks, folds each batch through the coalescer, and advances the live-tail cursor by the **original** last id of each batch (not the synthetic coalesced id). The live tail segment (the `while True` loop) and the `done`/`error` early-return are unchanged.

- [ ] **Step 1: Read the current replay segment**

Run: `cd backend && sed -n '305,345p' cubeplex/api/routes/v1/conversations.py`
Expected: see `target_event_id = await get_latest_event_id(...)`, `replay_start = ...`, the `iter_run_events(...)` call, and the replay `for` loop.

- [ ] **Step 2: Add imports + chunk-size constant**

Near the existing imports from `cubeplex.streams.run_events` in `conversations.py`, add `iter_run_events_chunked` to that import list, and import the coalescer:

```python
from cubeplex.streams.replay_coalescer import ReplayCoalescer
```

Add a module-level constant near the top of the file (after imports):

```python
# Replay backlog is read in bounded batches so a large reconnect never stalls
# the event loop. Tunable; ~1000 keeps each XRANGE + JSON decode cheap.
REPLAY_CHUNK_SIZE = 1000
```

- [ ] **Step 3: Replace the replay segment**

Replace this block (currently `conversations.py:326-340`):

```python
        if target_event_id is not None:
            replay_events = await iter_run_events(
                redis,
                prefix=prefix,
                run_id=run_id,
                start=replay_start,
                stop=target_event_id,
            )
            for event in replay_events:
                replay_cursor = event.event_id
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

        live_cursor = replay_cursor or target_event_id or "$"
```

with:

```python
        if target_event_id is not None:
            coalescer = ReplayCoalescer()
            async for batch in iter_run_events_chunked(
                redis,
                prefix=prefix,
                run_id=run_id,
                start=replay_start,
                stop=target_event_id,
                count=REPLAY_CHUNK_SIZE,
            ):
                for event in coalescer.feed(batch):
                    yield _format_sse_event(event.event_id, event.payload)
                    if event.payload.get("type") in {"done", "error"}:
                        return
                # Advance the live-tail cursor by the ORIGINAL last id of the
                # batch — coalesced events carry a synthetic (last-merged) id,
                # so the original id is what keeps the tail gap-free.
                replay_cursor = batch[-1].event_id
            for event in coalescer.flush():
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

        live_cursor = replay_cursor or target_event_id or "$"
```

> If `iter_run_events` is now unused in `conversations.py`, remove it from the import to keep the linter happy. Grep first: `grep -n iter_run_events cubeplex/api/routes/v1/conversations.py`.

- [ ] **Step 4: Verify type-check and lint pass**

Run: `cd backend && uv run mypy cubeplex/api/routes/v1/conversations.py && uv run ruff check cubeplex/api/routes/v1/conversations.py`
Expected: no errors.

- [ ] **Step 5: Run the existing conversations route tests**

Run: `cd backend && uv run pytest tests/ -k "stream or conversation or replay" -v`
Expected: PASS (no regressions in existing stream/replay tests).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/conversations.py
git commit -m "feat(stream): coalesce + chunk replay on reconnect"
```

---

## Task 4: Frontend yield in consume loops

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/web/__tests__/stores/messageStore.yield.test.ts`

Add a periodic yield so a large event batch can never pin the main thread. After coalescing this rarely triggers, but it caps the worst case (a single huge coalesced message) at "slow" instead of "frozen".

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/web/__tests__/stores/messageStore.yield.test.ts`:

```typescript
import { act } from '@testing-library/react'
import { useMessageStore } from '@cubeplex/core'

const CONV_ID = 'conv-yield'

function mockSSEResponse(events: object[]) {
  const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines))
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
}

const mockClient = { baseUrl: '', get: vi.fn(), post: vi.fn(), resolvePath: (p: string) => p }

beforeEach(() => {
  vi.useRealTimers()
  useMessageStore.setState({
    messages: {},
    streamAgents: {},
    isStreaming: false,
    streamingConversationId: null,
    currentRunId: null,
    lastAppliedEventId: null,
    statusPhase: null,
    error: null,
    todos: [],
    toolStartedMap: {},
    toolResultMap: {},
  })
})

it('yields to the event loop when processing many events', async () => {
  // 250 text deltas (> YIELD_EVERY=200) then done.
  const events = Array.from({ length: 250 }, (_, i) => ({
    type: 'text_delta',
    data: { content: 'x' },
    agent_id: null,
    agent_name: null,
    timestamp: '',
    event_id: `${i + 1}-0`,
  }))
  events.push({ type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' } as never)

  vi.stubGlobal('fetch', vi.fn(() => mockSSEResponse(events)))
  // Force the setTimeout fallback path (no scheduler.yield in jsdom).
  vi.stubGlobal('scheduler', undefined)
  const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')

  await act(async () => {
    await useMessageStore.getState().send(mockClient as never, CONV_ID, 'hi')
  })

  expect(setTimeoutSpy).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/web test -- messageStore.yield`
Expected: FAIL — `setTimeoutSpy` not called (no yield exists yet).

- [ ] **Step 3: Add the yield helper + constant**

In `frontend/packages/core/src/stores/messageStore.ts`, after the imports (near the top, before `export interface AgentStream`), add:

```typescript
const YIELD_EVERY = 200

function yieldToEventLoop(): Promise<void> {
  const sched = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler
  if (sched && typeof sched.yield === 'function') return sched.yield()
  return new Promise((resolve) => setTimeout(resolve))
}
```

- [ ] **Step 4: Add the yield to `consumeRunStream`**

In `consumeRunStream`, the loop is `for await (const event of streamRun(...)) { ... batchedSet((s) => applyStreamEvent(s, event)) }`. Add a counter declared just before the loop:

```typescript
  let processed = 0
```

and replace the trailing `batchedSet((s) => applyStreamEvent(s, event))` (the last statement inside the loop, after all the `if/else if` branches) with:

```typescript
      batchedSet((s) => applyStreamEvent(s, event))
      if (++processed % YIELD_EVERY === 0) {
        await yieldToEventLoop()
      }
```

- [ ] **Step 5: Add the same yield to `send`**

In `send`, the inner `for await (const event of streamSource)` loop ends with the same `batchedSet((s) => applyStreamEvent(s, event))`. Declare `let processed = 0` just before the `outer:` loop, and replace that trailing `batchedSet(...)` with the identical block:

```typescript
          batchedSet((s) => applyStreamEvent(s, event))
          if (++processed % YIELD_EVERY === 0) {
            await yieldToEventLoop()
          }
```

> Both loops have `continue` in the `injected_message` branch (before `batchedSet`), so the counter only advances on the batched path — the `flush()` → `__commitTurnAndInject` ordering is never split by a yield.

- [ ] **Step 6: Build core and run the test**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web test -- messageStore.yield`
Expected: PASS.

- [ ] **Step 7: Run the existing messageStore tests for regressions**

Run: `cd frontend && pnpm --filter @cubeplex/web test -- useMessages`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/web/__tests__/stores/messageStore.yield.test.ts
git commit -m "feat(messageStore): yield to event loop during large stream consumption"
```

---

## Final verification

- [ ] **Backend full module sweep**

Run: `cd backend && uv run pytest tests/unit/test_replay_coalescer.py tests/unit/test_iter_run_events_chunked.py && uv run pytest tests/ -k "stream or conversation or replay" -q`
Expected: all PASS.

- [ ] **Frontend type-check + lint**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web lint`
Expected: clean.

- [ ] **Manual self-test (user)**

Start a run that produces a lot of output; mid-stream, reload the page. Confirm: the page becomes interactive within a moment (clickable, scrollable), the transcript is complete, and streaming continues to completion. (E2E intentionally skipped per spec.)
