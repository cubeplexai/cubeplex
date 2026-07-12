# Resumable Run Streaming Design

## Problem

After the user submits a message, refreshing the page drops the in-flight stream. The page reload only reads checkpoint/history, so it can show persisted messages but cannot recover the current run's intermediate output or continue streaming.

The root causes are:

- The current `POST /messages` request owns the run lifecycle.
- Client disconnect cancels the run.
- Refresh recovery reads only checkpoint/history.

## Goal

After refresh:

- Recover the latest in-flight output for the current run.
- Continue streaming if the run is still active.
- Support multi-instance deployment.
- Do not require the frontend to persist full output locally.

## Non-Goals

- Recover arbitrary old in-flight runs after Redis retention expires.
- Reconstruct in-flight state from LangGraph checkpoints alone.
- Introduce a second live-state model such as a dedicated snapshot object.

## Solution

Use an **events-only** recovery model for the active run:

- `checkpoint/history` remains the source of truth for completed turns.
- `Redis` stores the complete ordered SSE event log for the current active run.
- On refresh, the page loads history, then replays the active run's events from Redis, then continues with live events.
- Replay and live streaming use the same frontend reducer.

This keeps one live-state representation: the SSE event stream already consumed by the frontend.

## Architecture

### 1. Durable state

LangGraph checkpoint/history stores:

- user messages
- completed assistant messages
- completed tool messages
- artifacts
- citations persisted in tool messages

It does **not** store token-level in-flight UI state.

### 2. Active run state

Redis stores:

- active run metadata per conversation
- ordered event log for the active run

Each Redis event is the same SSE message shape already emitted to the frontend:

- `text_delta`
- `reasoning`
- `tool_call`
- `tool_call_delta`
- `tool_result`
- `status`
- `citation`
- `artifact`
- `done`
- `error`

### 3. Run execution

Run execution is decoupled from the browser connection:

- sending a message creates a `run_id`
- a background worker executes `agent.astream(...)`
- each emitted SSE event is appended to Redis
- browser connections only subscribe to the run stream

Disconnecting a browser must not cancel the run by default.

## Data Model

### Active run metadata

Redis key:

- `conversation_active_run:{conversation_id}`

Fields:

- `run_id`
- `conversation_id`
- `status`
- `started_at`
- `first_event_id`
- `last_event_id`

### Run event log

Redis key:

- `run_events:{run_id}`

Storage:

- Redis Stream
- one entry per SSE event
- Redis Stream ID is the event ID

Retention:

- while run is active: retained
- after run completes: retain for 15 minutes

## Backend Flow

### 1. Send message

`POST /conversations/{id}/messages`

Flow:

1. Validate conversation.
2. Persist the user message to thread state.
3. Create `run_id`.
4. Register `conversation_active_run`.
5. Start background worker.
6. Return `run_id`.

The user message must be durable before publishing the active run.

### 2. Background worker

For each LangGraph stream event:

1. Convert to the frontend SSE payload.
2. Append to `run_events:{run_id}`.
3. Update `last_event_id` in active run metadata.
4. Push to connected subscribers.

On completion:

1. Persist final assistant/tool results through the normal checkpoint/history path.
2. Append terminal `done` or `error` event to Redis.
3. Remove `conversation_active_run`.
4. Keep `run_events:{run_id}` for a short TTL.

## API Changes

### `POST /conversations/{id}/messages`

Starts a run and returns:

```json
{
  "run_id": "..."
}
```

### `GET /conversations/{id}/bootstrap`

Returns:

- `messages`
- `active_run` or `null`

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

### `GET /conversations/{id}/runs/{run_id}/stream`

SSE subscription endpoint.

Behavior:

- if `Last-Event-ID` is absent: replay from the run's first event
- if `Last-Event-ID` is present: replay events with `event_id > Last-Event-ID`
- after replay reaches the server-side waterline, continue with live events

## Recovery Semantics

### Page load

1. Call `bootstrap`.
2. Render `messages` from history.
3. If there is no `active_run`, stop.
4. If there is an `active_run`, connect to its stream.
5. Replay the active run events through the same reducer used for live SSE.
6. Continue consuming live events.

### Same-client short reconnect

If the frontend has a recent `Last-Event-ID`, it may send it to reduce replay volume.

This is an optimization, not a correctness requirement.

### New client or refreshed page without cursor

The system must still work:

- replay from the current run's first event
- rebuild the current in-flight UI state
- continue with live events

## Consistency Rules

### 1. Replay source

Replay only covers the **current active run**.

Completed turns come from checkpoint/history. In-flight state comes from the active run event log.

### 2. Replay boundary

When a stream subscription starts, the server must record a replay waterline:

- `target_event_id = latest event id currently in Redis`

Recovery is:

1. replay all events up to `target_event_id`
2. switch to blocking live reads for events after `target_event_id`

This prevents gaps between replay and live streaming.

### 3. Deduplication

Frontend maintains:

- `current_run_id`
- `last_applied_event_id`

Rule:

- ignore any event whose `event_id <= last_applied_event_id`

This makes replay and live delivery idempotent.

### 4. Replay-safe reducer

The current SSE handling logic must be refactored into a replay-safe reducer:

- same input event stream must produce the same UI state
- replay and live must share the same state transition logic
- avoid unstable local fallbacks where possible

## Degradation

### Active run missing

If `conversation_active_run` is absent:

- treat the conversation as non-streaming
- render only checkpoint/history

### Active run exists but event log is unavailable

If `conversation_active_run` exists but `run_events:{run_id}` is missing or expired:

- render checkpoint/history
- do not attempt partial recovery of the missing in-flight state
- if live subscription can still attach, continue from current live output only

This is the explicit degradation behavior of the events-only design.

## Why This Design

Advantages:

- one live-state representation
- replay and live use the same reducer
- no separate snapshot schema to maintain
- new clients can still recover active runs as long as the event log exists

Tradeoffs:

- recovery depends on Redis event log availability
- replay/live boundary and dedup rules must be strict
- event log expiry weakens recovery compared with a snapshot-based design

## Files Likely Affected

- `backend/cubeplex/api/routes/v1/conversations.py`
- `backend/cubeplex/agents/stream.py`
- new backend run manager / Redis stream support module
- `frontend/packages/core/src/stores/messageStore.ts`
- `frontend/packages/core/src/api/stream.ts`
- `frontend/packages/web/components/chat/MessageList.tsx`
