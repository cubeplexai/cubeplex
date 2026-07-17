# Reconnect replay coalescing — design

Date: 2026-05-26
Status: approved (brainstorming), pending implementation plan

## Problem

When an agent run is producing a lot of output and the user refreshes the
page mid-run, the browser freezes: it pops the "Page Unresponsive" dialog,
and clicking "Wait" leaves the page stuck — clicks do nothing.

### Root cause

On refresh the frontend reconnects to the active run and **replays the entire
event log from event 0**:

- `messageStore.loadMessages` finds `active_run` in the bootstrap response and
  reconnects via `consumeRunStream(..., lastEventId=undefined, ...)`
  (`frontend/packages/core/src/stores/messageStore.ts:846`), resetting
  `lastAppliedEventId` to `null` (`messageStore.ts:833`). bootstrap returns
  `active_run.last_event_id` but it is **not used** — full replay is
  intentional, because the in-flight assistant turn is not in the trimmed
  bootstrap history (`trimHistoryForActiveRun`, `messageStore.ts:255`).
- A long run emits tens of thousands of fine-grained `text_delta` /
  `tool_call_delta` events. Each is folded by `applyStreamEvent` with
  immutable spreads (`appendTextBlock` copies the blocks array,
  `{...state.streamAgents}`, `{...toolResultMap}` —
  `messageStore.ts:357-501`). Total client cost is **O(N²)** in event count,
  plus a giant React re-render of the accumulated transcript.
- The backend amplifies this: `iter_run_events` does a single
  `redis.xrange(min, max)` over the whole stream (up to 1,000,000 events,
  `run_manager.py:439`) and the replay loop yields every event with no pacing
  (`conversations.py:327-338`).
- The consume loop never yields to the event loop, so input/paint handlers
  never run → the page is pinned → "Wait" re-enters the same blocked task.

### What we are NOT doing (and why)

An earlier idea was to render completed messages from the checkpointer and
tail Redis from a cursor stamped into each checkpointed message (requiring a
cubepi-generated run-scoped event id, an explicit-`XADD`-id scheme or a
`token → stream-id` map, and special handling for compaction / synthetic /
pre-migration messages). That precisely avoids *re-sending* completed content,
but the freeze is not caused by re-sending — it is caused by re-sending
**tens of thousands of 1-character deltas** and folding them O(N²) on the
client. Coalescing the replayed events server-side removes the root cause with
no cubepi change, no migration, and no checkpoint correlation. The
checkpoint-cursor architecture is dropped.

## Approach

Two changes, server-side coalescing as the core fix and a frontend yield as a
safety net.

1. **Server-side replay coalescing (core).** When replaying the historical
   backlog on reconnect, fold the fine-grained events into a compact set
   before sending. Consecutive `text_delta` / `reasoning` per agent collapse
   into one event; `tool_call_delta` per `(agent_id, index)` collapses into a
   single `tool_call_delta` (concatenated args); structural events pass
   through unchanged. The
   replayed payload shrinks from tens of thousands of events to a few dozen.
   The **live tail** (events after the reconnect snapshot point) stays
   fine-grained so streaming feels smooth.

2. **Frontend yield (safety net).** The consume loops yield to the event loop
   periodically so the browser never shows "Page Unresponsive", even if a
   single coalesced message is still very large.

The replay read is **chunked and incremental** so the backend never blocks the
asyncio event loop while handling a large backlog.

## Component 1 — server-side replay coalescing

### Where

Only the **replay segment** of `event_generator`
(`backend/cubeplex/api/routes/v1/conversations.py:321-365`). The **live tail**
segment (lines 341-365) is unchanged. The `done`/`error` short-circuit
(`conversations.py:337`) is preserved.

### `ReplayCoalescer` — stateful streaming folder

A new pure (no IO) helper at `backend/cubeplex/streams/replay_coalescer.py`,
unit-testable in isolation. It is **streaming** (not list-in/list-out) so it can fold across
read chunks without holding the whole backlog in memory:

- `feed(events: list[RunEvent]) -> list[RunEvent]` — accept a chunk, return
  the coalesced events that are now complete; hold in-progress runs in a
  pending buffer.
- `flush() -> list[RunEvent]` — emit everything still buffered (end of
  replay).

Pure-function equivalence holds: `feed(all) + flush()` produces the same
output regardless of how `all` is split across `feed` calls.

### Coalescing rules

| Event type | Rule |
|---|---|
| `text_delta` | Per `agent_id`, concatenate `data.content` of a consecutive run into one event. |
| `reasoning` | Per `agent_id`, concatenate `data.content` of a consecutive run into one event. |
| `tool_call_delta` | Per `(agent_id, index)`, concatenate `data.args_delta` into one event that stays `type: "tool_call_delta"` (keeps a tool call that was mid-stream at the snapshot point visible). **Not** `tool_call_streaming` — see below. |
| `tool_call`, `tool_result`, `usage`, `done`, `error`, `citation`, `artifact`, `injected_message`, `status` | Pass through unchanged. |

**Flush is strictly stream-order, not per-agent.** A pending run (text /
thinking for an agent, or a `tool_call_delta` accumulation for an
`(agent_id, index)`) is flushed **before emitting any later event that would
pass it in stream order** — i.e. coalesce only *adjacent* same-key deltas,
never across an intervening event belonging to a different agent or key. This
is the critical correctness rule: the frontend applies replayed events in SSE
order and dedups by `event_id`, so merging a run *across* an intervening event
would reorder or drop visible content. Concretely, for `main e1`, `subagent
e2`, `main e3`, the coalescer flushes the `main` pending run (`e1`) before
emitting `e2`, then starts a fresh `main` run for `e3` — yielding `e1`, `e2`,
`e3` in order, never `e1+e3` around `e2`. All pending runs are also flushed at
`flush()`.

The `tool_call_delta` coalescing keeps the SSE `type` as `tool_call_delta`
(with concatenated `args_delta`), **not** `tool_call_streaming`.
`tool_call_streaming` is a frontend *content-block* type, not an SSE event
type — `applyStreamEvent` only consumes `tool_call_delta` and builds the
`tool_call_streaming` block locally (`messageStore.ts:422`). Emitting a
`tool_call_streaming` event would be ignored by the frontend, so the
already-streamed args prefix would not render until the final `tool_call`
arrives.

### Correctness

1. Folding happens in stream order.
2. The frontend already merges deltas into the **last block** of the agent's
   stream (`appendTextBlock` / `appendThinkingBlock`,
   `messageStore.ts:155-180`), so a few large coalesced events render
   **identically** to many small ones. No frontend change in this component.
3. Multi-agent interleaving: a pending run is keyed per `agent_id`, but is
   flushed the moment any event of a different key arrives (the strict
   stream-order rule above), so different agents never merge and SSE order is
   preserved exactly. Subagent events (`agent_id="subagent:<tool_call_id>"`)
   thus fold independently from the main agent without ever reordering across
   each other.
4. The accumulation itself must be O(N), not O(N²). A pending run holds its
   delta pieces in a **list** and `"".join()`s once when the run is emitted —
   never `accumulated = accumulated + delta` per event (that copies the whole
   growing string each time and would re-introduce the very backend stall we
   are removing).

### Bounded coalesced size

A pending text/thinking/tool-arg run is also flushed when its accumulated
length reaches `MAX_COALESCED_CHARS` (a module constant, ~64 KiB), then a fresh
run with the same key continues. So a single enormous message is emitted as a
handful of bounded `text_delta` events rather than one giant one. This matters
for two reasons:

- It keeps each `"".join()` and each frontend reducer/render step bounded, and
- it keeps the **frontend yield** (Component 2) effective: the yield is
  count-based, so it only fires across multiple events. A single multi-hundred-
  KB coalesced event would slip the net and still pin the main thread in one
  `applyStreamEvent` + render. Capping size on the backend guarantees the
  frontend always sees multiple bounded events for a large message.

The split is invisible in the rendered transcript: the frontend merges
consecutive same-agent `text_delta`s into the same block
(`appendTextBlock`, `messageStore.ts:155-180`), so N bounded deltas render
identically to one.

### Synthetic event ids

Coalesced events have no real Redis entry id. Each emitted coalesced event is
stamped with the `event_id` of the **last original event it merged**. This
keeps the frontend's `compareEventIds` dedup monotonic
(`messageStore.ts:95-106`): the first live-tail event's id is strictly greater
than the last coalesced event's id, so nothing is wrongly deduped.

### `done` / `error` in the replay segment

The coalescer terminates immediately on `done`/`error`, emitting it as the
final event; nothing after it is folded in. The route keeps its existing
short-circuit `return`.

### Chunked / incremental read (do not block the event loop)

The current path loads the whole backlog at once: `iter_run_events` →
`redis.xrange(min, max)` → `_decode_stream_entries` JSON-parses every entry
synchronously (`run_events.py:413-421, 383-388`). For tens of thousands of
events this is one large synchronous block that stalls the asyncio event loop
and every other request on the worker.

Change the replay segment to read in cursor-paginated chunks:

- Add a chunked reader (e.g. `iter_run_events_chunked`) that pages with
  `XRANGE (last_id <stop> COUNT N`, yielding one decoded batch at a time.
  Do **not** change `iter_run_events` or its other callers.
- Loop: read a chunk → `coalescer.feed(chunk)` → `yield` coalesced events →
  advance cursor → repeat until the snapshot `target_event_id` is reached →
  `coalescer.flush()` → `yield` remainder. Each `await xrange` returns control
  to the event loop; memory is bounded to one chunk; the client starts
  receiving coalesced output sooner.
- `N` (chunk size) is a named constant, ≈1000, tunable.
- The replay cursor advances by the **original** last event id seen (not a
  synthetic coalesced id), so the live tail picks up exactly where the
  snapshot ended — no gap, no duplicate.

## Component 2 — frontend yield (safety net)

### Where

Both consume loops in `frontend/packages/core/src/stores/messageStore.ts`:
`consumeRunStream` (`messageStore.ts:686`) and `send` (`messageStore.ts:924`).

### Behavior

Count processed events; every `YIELD_EVERY` events, yield to the event loop:

```ts
let processed = 0
for await (const event of streamRun(...)) {
  // existing artifact / citation / error / done / injected_message branches unchanged
  batchedSet((s) => applyStreamEvent(s, event))
  if (++processed % YIELD_EVERY === 0) {
    await (globalThis.scheduler?.yield?.() ?? new Promise((r) => setTimeout(r)))
  }
}
```

- `YIELD_EVERY = 200` (named constant, tunable).
- Prefer `scheduler.yield()` (less likely to be deprioritized); fall back to
  `setTimeout(0)`.
- The yield point sits **after** `batchedSet`, and must not split the
  `injected_message` `flush()` → `__commitTurnAndInject` sequence
  (`messageStore.ts:741-751`). Only yield between ordinary batched events.

### Relationship to coalescing

After coalescing, the normal case has few events and rarely reaches a yield
point. The yield's effectiveness **depends on the backend size cap** (see
"Bounded coalesced size"): because the yield is count-based, a single huge
coalesced event would never trigger it. The cap guarantees a large message
arrives as multiple bounded `text_delta`s, so the count yield fires between
them and each `applyStreamEvent` + render step stays bounded — worst case is
"slow", not "frozen / unclickable". `send` gets the same treatment for
consistency (zero cost; no backlog on a fresh turn).

## Edge cases

1. **Tool call mid-stream at reconnect.** Replay folds the arrived
   `tool_call_delta`s into one `tool_call_delta` event (concatenated
   `args_delta`, same `type`); the frontend builds the `tool_call_streaming`
   block locally from it; the live tail continues the rest; the final
   `tool_call` event makes the frontend replace the streaming block with the
   complete call (`appendToolCallBlock`, `messageStore.ts:182-209`). No loss,
   eventually consistent.
2. **Empty stream / empty coalescer output.** `feed`/`flush` on no input
   return nothing; existing live-tail / heartbeat logic runs unchanged.
3. **compaction summary / synthetic / pre-migration messages.** Not special —
   the replay reads the raw Redis stream and never correlates with the
   checkpointer, so these "just work".
4. **Chunk boundary splits a run.** Handled by the stateful coalescer: a
   text/thinking run or a `tool_call_delta` accumulation that spans chunks is
   held in the pending buffer across `feed` calls and flushed at the right
   boundary.

## Testing

Per CLAUDE.md (E2E priority, incremental per-module tests). The reconnect E2E
is intentionally **skipped** — the user will self-test the freeze-on-reload
path manually.

### Backend — `ReplayCoalescer` unit tests (primary)

1. Consecutive `text_delta` (same agent) → one event, content concatenated.
2. Consecutive `reasoning` (same agent) → one event, content concatenated.
3. `tool_call_delta` per `(agent_id, index)` → one `tool_call_delta` (same
   `type`), `args_delta` concatenated (mid-stream tool call).
4. `tool_call` / `tool_result` / `usage` / `done` / `error` / `citation` /
   `artifact` / `injected_message` / `status` pass through unchanged.
5. Multi-agent interleave preserves stream order: `main e1`, `subagent e2`,
   `main e3` → emits `e1`, `e2`, `e3` (the `main` run is flushed before `e2`),
   **never** `e1+e3` merged around or after `e2`.
6. Coalesced event id = id of the last original event it merged (dedup
   monotonicity).
7. `done` / `error` terminates immediately and is the final event.
8. Empty input → empty output.
9. Text interrupted by a tool call → two separate text blocks (tool_call in
   between).
10. **Chunk-boundary invariance:** for the same event sequence, `feed`/`flush`
    with arbitrary chunk splits yields the same output as feeding all at once
    (especially text runs and `tool_call_delta` accumulations spanning a
    boundary).
11. **Size cap splits a huge run:** a single run far exceeding
    `MAX_COALESCED_CHARS` is emitted as multiple bounded `text_delta` events
    (each ≤ ~cap + one delta), and concatenating their content reproduces the
    original — the one-huge-event case the frontend yield can't handle alone.

### Frontend — yield loop unit test

`consumeRunStream` processing more than `YIELD_EVERY` events hits a yield
point (assert `scheduler.yield` / `setTimeout` invoked), and the yield does
not break the `injected_message` flush/commit ordering.

## Files touched

- `backend/cubeplex/streams/replay_coalescer.py` — `ReplayCoalescer` (new).
- `backend/cubeplex/streams/run_events.py` — add chunked reader; leave
  `iter_run_events` and callers untouched.
- `backend/cubeplex/api/routes/v1/conversations.py` — replay segment of
  `event_generator` uses the chunked reader + coalescer.
- `frontend/packages/core/src/stores/messageStore.ts` — yield in both consume
  loops.
- Backend + frontend unit tests as above.
