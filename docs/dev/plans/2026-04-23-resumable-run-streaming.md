# Resumable Run Streaming Implementation Plan

> **For implementation:** follow this plan top-down. Steps use checkbox syntax for tracking. Keep the reducer and wire format unified: Redis replayed events and live SSE events must go through the same frontend state transition logic.

**Goal:** After refresh, recover the current active run by replaying its Redis-backed SSE event log, then continue live streaming if the run is still active.

**Architecture:** LangGraph checkpoint/history remains the durable baseline for completed turns. Redis stores the active run pointer and an ordered per-run event log. A background run manager executes `agent.astream(...)` independently of the browser connection. Frontend recovery loads history first, then replays active-run events through the same reducer used for live SSE.

**Tech Stack:** FastAPI, LangGraph, MySQL checkpointer, Redis Streams, Next.js, React, TypeScript, Zustand

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/cubeplex/streams/run_events.py` | Redis-backed active-run metadata and per-run event log primitives |
| `backend/cubeplex/streams/run_manager.py` | Run lifecycle orchestration decoupled from HTTP connection |
| `frontend/packages/core/src/api/runStreams.ts` | Bootstrap and run stream client helpers |

### Modified Files

| File | Changes |
|------|---------|
| `backend/pyproject.toml` | Add Redis client dependency if not already present |
| `backend/cubeplex/api/app.py` | Initialize shared Redis resources for stream management |
| `backend/cubeplex/api/routes/v1/conversations.py` | Split send/bootstrap/stream responsibilities; stop tying run lifetime to SSE request |
| `backend/cubeplex/agents/stream.py` | Reuse existing SSE event conversion for persisted run events |
| `frontend/packages/core/src/types/events.ts` | Add `event_id` to streamed event type |
| `frontend/packages/core/src/stores/messageStore.ts` | Extract replay-safe reducer; support replay + live event application |
| `frontend/packages/web/components/chat/MessageList.tsx` | Drive recovery through bootstrap + run stream |
| `frontend/packages/core/src/api/stream.ts` | Narrow to low-level stream reader or migrate to `runStreams.ts` |

---

## Task 1: Backend Foundation — Add Redis-backed active run and event log primitives

**Files:**
- Add: `backend/cubeplex/streams/run_events.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Add Redis dependency**

Add the Python Redis client dependency if it is not already in `backend/pyproject.toml`.

- [ ] **Step 2: Implement active-run metadata API**

Create `backend/cubeplex/streams/run_events.py` with helpers for:

- `get_active_run(conversation_id)`
- `set_active_run(conversation_id, run_id, status, first_event_id=None, last_event_id=None)`
- `clear_active_run(conversation_id, run_id)`
- `append_run_event(run_id, event_json) -> event_id`
- `iter_run_events(run_id, start=None, stop=None)`
- `get_latest_event_id(run_id)`
- `expire_run_events(run_id, ttl_seconds)`

Requirements:

- use Redis Streams for `run_events:{run_id}`
- use a separate key for `conversation_active_run:{conversation_id}`
- store `first_event_id` and `last_event_id`
- keep event payload identical to frontend SSE payload

- [ ] **Step 3: Initialize shared Redis client**

In `backend/cubeplex/api/app.py`, initialize a shared Redis client on app startup and store it on `app.state`.

Requirements:

- fail fast if Redis is required but unavailable
- close the client cleanly on shutdown

---

## Task 2: Backend Runtime — Decouple run execution from the HTTP stream request

**Files:**
- Add: `backend/cubeplex/streams/run_manager.py`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`

- [ ] **Step 1: Implement run manager**

Create `backend/cubeplex/streams/run_manager.py` that:

- creates a `run_id`
- registers active run metadata
- starts a background task to execute `agent.astream(...)`
- persists every SSE event to Redis before fanout
- appends terminal `done` or `error`
- clears active-run metadata on completion
- retains the run event stream for 15 minutes after completion

The worker must outlive any individual browser connection.

- [ ] **Step 2: Refactor `POST /messages`**

Change `POST /messages` so it:

1. validates the conversation
2. persists the user message to thread state
3. starts a run via the run manager
4. returns `{"run_id": "..."}` instead of owning the entire SSE lifecycle

Do not keep the current request-scoped `event_q` / `stream_task` model as the primary execution path.

- [ ] **Step 3: Add bootstrap endpoint**

Add:

`GET /conversations/{conversation_id}/bootstrap`

Return:

- `messages` from checkpoint/history
- `active_run` if present

Example:

```json
{
  "messages": [...],
  "active_run": {
    "run_id": "...",
    "status": "running"
  }
}
```

- [ ] **Step 4: Add replay-capable run stream endpoint**

Add:

`GET /conversations/{conversation_id}/runs/{run_id}/stream`

Behavior:

- if `Last-Event-ID` is absent, replay from the run's first event
- if present, replay events with `event_id > Last-Event-ID`
- capture a replay waterline before replay begins
- after replay reaches that waterline, switch to live blocking reads

Requirements:

- event IDs exposed to the client must be Redis Stream IDs
- event ordering must be stable
- live connection must not re-emit already replayed events

---

## Task 3: Backend Semantics — Preserve consistency and degrade cleanly

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: `backend/cubeplex/streams/run_manager.py`
- Modify: `backend/cubeplex/streams/run_events.py`

- [ ] **Step 1: Define and enforce replay boundary**

At subscription start:

- read `target_event_id = latest event id currently in Redis`
- replay only up to `target_event_id`
- then switch to blocking reads for events after `target_event_id`

This avoids gaps between replay and live.

- [ ] **Step 2: Define degradation behavior**

If `conversation_active_run` exists but the Redis event log is missing or expired:

- return checkpoint/history only
- do not attempt partial in-flight reconstruction
- if live attachment is still possible, continue from current live output only

- [ ] **Step 3: Keep history/event boundary clean**

Ensure:

- completed turns come only from checkpoint/history
- active-run replay covers only the current active run
- no completed turn is replayed from Redis after it has become durable history

---

## Task 4: Frontend Store — Extract a replay-safe event reducer

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Modify: `frontend/packages/core/src/types/events.ts`

- [ ] **Step 1: Add `event_id` to streamed event typing**

Extend streamed frontend event types so replayed and live events carry an `event_id`.

- [ ] **Step 2: Extract pure event application logic**

Refactor `messageStore` so the current SSE handling becomes a replay-safe reducer, e.g.:

- `applyStreamEvent(state, conversationId, event)`

Requirements:

- replay and live use the exact same transition logic
- no duplicate append behavior when the same event is seen twice
- avoid unstable local fallbacks where possible
- keep current UI semantics for:
  - `text_delta`
  - `reasoning`
  - `tool_call`
  - `tool_call_delta`
  - `tool_result`
  - `status`
  - `citation`
  - `artifact`

- [ ] **Step 3: Track replay progress**

Store:

- `currentRunId`
- `lastAppliedEventId`

Rule:

- ignore any event with `event_id <= lastAppliedEventId`

---

## Task 5: Frontend API — Add bootstrap and run stream clients

**Files:**
- Add: `frontend/packages/core/src/api/runStreams.ts`
- Modify: `frontend/packages/core/src/api/stream.ts`

- [ ] **Step 1: Add bootstrap client**

Implement:

- `getConversationBootstrap(client, conversationId)`

- [ ] **Step 2: Add run stream client**

Implement:

- `streamRun(client, conversationId, runId, lastEventId?)`

Requirements:

- parse SSE lines
- surface `event_id`
- send `Last-Event-ID` when provided

- [ ] **Step 3: Keep no-cursor recovery supported**

The client must work without `Last-Event-ID`:

- full replay of current run
- then continue live

`Last-Event-ID` is an optimization only.

---

## Task 6: Frontend Page Flow — Recover from bootstrap, replay, then live stream

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Modify: `frontend/packages/web/hooks/useMessages.ts`

- [ ] **Step 1: Replace page-load recovery flow**

On page load:

1. call `bootstrap`
2. render history messages
3. if no `active_run`, stop
4. if `active_run` exists, connect to run stream
5. replay current run events through the reducer
6. continue consuming live events

- [ ] **Step 2: Preserve current UX behavior**

Ensure replayed state renders correctly for:

- assistant in-flight text
- tool call streaming blocks
- subagent cards
- todo panel / progress card
- status message
- tool result preview map

- [ ] **Step 3: Support same-client short reconnect optimization**

If the page already knows `lastAppliedEventId`, send it to the stream endpoint to reduce replay volume.

Do not make this required for correctness.

---

## Task 7: Verification — Add backend and frontend tests for refresh recovery

**Files:**
- Modify/add backend tests near conversation streaming coverage
- Modify/add `frontend/packages/web/__tests__/hooks/useMessages.test.ts`
- Modify/add `frontend/packages/web/__tests__/e2e/streaming.spec.ts`

- [ ] **Step 1: Backend tests**

Cover:

- active run metadata lifecycle
- Redis event append + ordered replay
- replay waterline prevents gaps
- stream endpoint supports both:
  - no `Last-Event-ID`
  - with `Last-Event-ID`

- [ ] **Step 2: Frontend store tests**

Cover:

- replaying a sequence of historical run events reconstructs the same state as live streaming
- duplicate events are ignored by `event_id`
- reducer remains correct for `tool_call_delta` and `tool_result`

- [ ] **Step 3: E2E refresh test**

Flow:

1. start a long enough streaming run
2. refresh the page mid-run
3. verify earlier in-flight output is restored
4. verify new output continues to stream
5. verify final state lands in history after completion

---

## Task 8: Cleanup — Remove request-owned streaming assumptions

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: any now-obsolete frontend stream helpers

- [ ] **Step 1: Remove request-scoped run ownership**

Delete or isolate the old logic that assumes:

- the SSE HTTP request owns the run
- disconnect should cancel the run

- [ ] **Step 2: Keep durable history path unchanged**

Do not regress existing history loading from checkpoint/history for completed turns.

- [ ] **Step 3: Final verification**

Run targeted checks and record results in the PR or final summary.

Suggested commands:

```bash
uv run pytest backend/tests -q
pnpm --dir frontend/packages/web test
pnpm --dir frontend/packages/web build
```

---

## Implementation Notes

- Treat Redis Stream ID as the canonical `event_id`.
- Do not introduce a separate snapshot schema in this plan.
- If replay-safe reducer extraction reveals unstable local fallbacks, prefer using server timestamps already present in the event payloads.
- If a future iteration needs stronger degraded recovery after Redis expiry, that should be a separate design change rather than a hidden addition during implementation.
