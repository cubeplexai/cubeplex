# Steer message display: pending → committed

**Date:** 2026-05-25
**Status:** Design (approved in brainstorming, pending spec review)
**Branch:** `feat/steer-message-display`

## Problem

When a user steers a running agent (types a mid-run instruction), the steer
message is shown optimistically as a normal user bubble appended to the end of
the in-memory message list. During an active run, the live assistant output
lives in a single streaming bubble (`streamAgents['main']`) that has not yet
been split into per-turn messages. So the optimistic steer bubble renders
*above* the still-streaming assistant output.

cubepi actually injects the steer `UserMessage` into the thread at the next
safe point — after the current tool batch completes, before the next assistant
turn (`cubepi/agent/loop.py:321-328` and `:334-341`). On reload, the
checkpointer returns the thread with the steer message **interleaved between
assistant turns**, not at the top or bottom of one merged bubble. Result: the
steer message visibly **jumps position** on refresh.

Root cause of the jump: the frontend commits the steer message to a *guessed*
transcript position before it knows the real injection point. cubebox also does
not forward any SSE event when cubepi injects the message
(`backend/cubebox/agents/stream.py:120-153` drops `MessageStart`/`MessageEnd`
for non-assistant messages), so the frontend has no live signal of the real
position.

## Goal

Adopt Claude Code's queued-message model:

1. **Pending state** — a sent-but-not-yet-injected steer shows as a distinct,
   dimmed item pinned **above the input box**, not in the transcript. The
   transcript stream stays clean; no position is ever claimed and then revoked.
2. **Committed state** — when cubepi actually injects the message, the backend
   emits a new SSE event. The frontend finalizes the current streaming bubble
   into a history assistant message, inserts the steer user message at that
   point, resets the streaming bubble, and removes the pending item. The live
   position now matches what a reload shows — **no jump**.
3. **Cancel** — a pending steer can be cancelled (best-effort) before cubepi
   drains it, via an X on the pending item.

Multiple steers may be pending at once; they stack as a vertical list above the
input box.

## Non-goals

- Follow-up messages (`get_follow_up_messages`) — cubebox does not use them.
  The backend event mechanism is generic enough to cover them later, but no UI
  is built for them now.
- Editing a pending steer in place. Cancel + retype only.

## Join key: `steer_id`

A client-generated `steer_id` (UUID-ish, minted when the user sends the steer)
is the single join key across the whole flow:

- Sent on the steer request; cubebox puts it in the injected
  `UserMessage.metadata["steer_id"]`. cubepi's `UserMessage` has no `id` field
  but does carry a `metadata: dict`, so this avoids a Message-schema change
  upstream and follows the existing "cubebox extras ride in metadata"
  convention.
- The `injected_message` SSE event echoes `steer_id`, so the frontend can match
  the committed message to the pending item and remove it.
- Cancel targets a `steer_id`; cubepi's steering queue removes the queued
  message whose `metadata["steer_id"]` matches.

The persisted message id (assigned by cubebox when it stores history) and the
live committed message id may differ; this is fine — `steer_id` is the live
join key, and a reload does a full re-render from authoritative history.

## Architecture & changes

### A. cubepi (upstream) — cancel support

`cubepi/agent/agent.py`:

- `_MessageQueue.remove(steer_id: str) -> bool` — drop queued message(s) whose
  `metadata.get("steer_id") == steer_id`; return whether anything was removed.
- `Agent.cancel_steer(steer_id: str) -> bool` — delegate to
  `self._steering_queue.remove(steer_id)`.

No change to the `UserMessage` schema. No change to drain/injection behavior.

### B. cubebox backend — `injected_message` SSE event

`backend/cubebox/agents/stream.py` (`convert_agent_event_to_sse`) +
`backend/cubebox/streams/run_manager.py` (`_on_event`):

- When a `MessageEndEvent` wraps a `UserMessage`, emit:
  ```json
  {"type": "injected_message", "role": "user",
   "content": "<text>", "steer_id": "<from metadata>"}
  ```
  (Text extracted from `TextContent` blocks; steer is text-only.)
- **Seed-message dedup:** cubepi emits `MessageStart`/`MessageEnd` for the
  run's seed prompt at loop start (`cubepi/agent/loop.py:62-64`). cubebox sends
  exactly one seed user message per run, and the frontend already shows it
  optimistically. `_on_event` keeps a per-run counter and **skips the first
  user-message event**, forwarding only subsequent injected ones. (A steer
  drained by the start-poll before the first assistant turn is the 2nd
  user-message event → forwarded correctly.)

### C. cubebox backend — cancel-steer endpoint + control plane

`backend/cubebox/api/routes/v1/conversations.py`:

- `SteerMessageRequest` gains `steer_id: str` (client-generated, required).
- New route `POST /{conversation_id}/steer/cancel` with body `{steer_id}`,
  returning a status (`cancelled` | `not_found`). `not_found` = already drained
  or unknown id.

`backend/cubebox/streams/run_manager.py`:

- `dispatch_steer` already forwards `content`; thread `steer_id` through so the
  injected `UserMessage` carries `metadata["steer_id"]`.
- Add `dispatch_cancel_steer(run_id, steer_id)` mirroring the existing
  steer/cancel multi-instance plumbing (local agent call + Redis pub/sub
  control message `cancel_steer` for cross-instance, alongside the existing
  `steer`/`cancel` control types at `run_manager.py:596`).

### D. frontend core — types + parsing

- Add `InjectedMessageEvent` to the `AgentEvent` union (`types`), and parse the
  `injected_message` SSE type in `frontend/packages/core/src/api/stream.ts`.
- `steerRun(client, conversationId, content, steerId)` — send `steer_id`.
- New `cancelSteer(client, conversationId, steerId)` API call.

### E. frontend core — `messageStore`

`frontend/packages/core/src/stores/messageStore.ts`:

- New state: `pendingSteers: Record<conversationId, { steerId: string; text: string }[]>`.
- `steer()`:
  - mint `steerId`, push `{steerId, text}` into `pendingSteers[convId]`
    (instead of appending to `messages`).
  - call `steerRun(..., steerId)`. On `no_active_run` or error → remove that
    `steerId` from `pendingSteers` (rollback).
- New `cancelSteer(client, conversationId, steerId)` store action:
  - optimistically remove from `pendingSteers`; call the cancel API. If the API
    reports `not_found` (already drained), the upcoming `injected_message` will
    commit it normally — acceptable best-effort behavior.
- Handle `injected_message` in **both** stream consumers (`send()` loop and
  `consumeRunStream()`), via a shared helper `commitTurnAndInject(injected)`:
  1. Build the current main turn's messages from `streamAgents['main']` using a
     pure helper `buildTurnMessages(agents, toolResultMap, turnUsage)` extracted
     from `finalizeCompletedStream` (so assistant + tool_result + subagent
     handling is identical), append them to `messages[convId]`.
  2. Append the injected steer user message (id derived from `steer_id`).
  3. Reset `streamAgents['main']` to empty so subsequent deltas form a fresh
     bubble. Leave `toolResultMap`/`toolStartedMap` intact (keyed globally by
     tool_call_id).
  4. Remove the matching `steerId` from `pendingSteers`.
- `finalizeCompletedStream()` — refactor to reuse `buildTurnMessages`. On run
  finalize, clear `pendingSteers[convId]` (run is over; any still-pending were
  not injected, and reload is authoritative if that's wrong).
- `loadMessages()` — clear `pendingSteers[convId]` on (re)load; committed steers
  come back from checkpointer history naturally → no transcript duplication.

### F. frontend web — UI

- New pending area rendered **above the textarea** in/near
  `components/layout/InputBar.tsx`: maps `pendingSteers[convId]` to a vertical
  stack of dimmed chips, each with the steer text and a cancel (X) button. Style
  borrows Claude Code's `QueuedMessageProvider` dimmed treatment (own component,
  not a `mode` prop — per the repo's module-reuse rule).
- Remove the old optimistic steer bubble from the transcript (no longer appended
  to `messages`).
- `components/chat/MessageList.tsx` — no special case needed: a committed steer
  is a normal `role==='user'` message in `messages[]` at the correct position.

## Data flow (happy path)

```
user types mid-run, hits Enter
  → store.steer(): mint steer_id, push to pendingSteers, POST /steer {content, steer_id}
  → pending chip appears above input (dimmed)
... agent finishes current tool batch ...
cubepi drains steering queue → injects UserMessage(metadata.steer_id) → MessageEnd(UserMessage)
  → cubebox _on_event (past seed) → SSE injected_message {content, steer_id}
  → store: commitTurnAndInject:
       finalize current main bubble → append to messages[]
       append steer user message at this point
       reset streamAgents.main
       remove steer_id from pendingSteers   (chip disappears)
  → next deltas build the next assistant bubble below the steer message
reload → checkpointer history has the same interleaving → identical position
```

## Cancel flow

```
user clicks X on a pending chip
  → store.cancelSteer(steer_id): remove from pendingSteers, POST /steer/cancel {steer_id}
  → run_manager.dispatch_cancel_steer → agent.cancel_steer(steer_id)
       → queue.remove(steer_id)
  → cancelled  : never injected, no injected_message ever arrives. Done.
  → not_found  : already drained → injected_message will arrive and commit it
                 into the transcript (best-effort cancel lost the race). Acceptable.
```

## Error handling & edges

- **steer arrives too late** (run finishing a tool-less final turn): cubepi's
  tail drain (`loop.py:334-341`) still injects it → `injected_message` fires →
  committed. No special handling.
- **run dies before injection**: pending chip lingers; cleared on
  `finalizeCompletedStream` / next `loadMessages`. Reload is authoritative.
- **identical-text steers**: disambiguated by `steer_id`, not text.
- **multi-instance**: cancel uses the same Redis control channel as steer/cancel
  so it works when the run lives on another API instance.
- **reconnect mid-run** (`consumeRunStream` replay): `injected_message` events
  carry `event_id` and flow through the same de-dup/ordering as other events;
  the commit helper is idempotent w.r.t. already-applied event ids.

## Testing

- **cubepi unit:** `_MessageQueue.remove` (match/no-match, mode=all & one);
  `Agent.cancel_steer` returns correct bool.
- **cubebox backend unit:** `convert_agent_event_to_sse` emits
  `injected_message` for an injected UserMessage and **not** for the seed;
  `_on_event` seed-skip counter; cancel endpoint status mapping.
- **frontend core unit (vitest):** `steer()` writes to `pendingSteers` not
  `messages`; `injected_message` commits the current turn, inserts the user
  message, resets `main`, removes the pending entry; `cancelSteer` removes
  optimistically; `loadMessages`/`finalize` clear pending.
- **frontend web unit:** pending list renders chips + cancel; chip clears on
  commit.
- **E2E (priority, per repo discipline):** real run, steer mid-run →
  - pending chip appears above input,
  - chip moves into the transcript at the correct interleaved position once
    injected,
  - reload → identical position (no jump),
  - cancel before injection → message never enters the transcript.
  Extends the existing steer E2E. Needs a real agent run (the multi-instance
  run-control work already exercises live steer).

## Files touched

| Area | File |
|---|---|
| cubepi | `cubepi/agent/agent.py` (`_MessageQueue.remove`, `Agent.cancel_steer`) |
| backend | `cubebox/agents/stream.py`, `cubebox/streams/run_manager.py`, `cubebox/api/routes/v1/conversations.py` |
| core | `src/types`, `src/api/stream.ts`, `src/stores/messageStore.ts` |
| web | `components/layout/InputBar.tsx` (+ new pending component), `components/chat/MessageList.tsx` (remove optimistic), tests |

## PR split

cubepi change ships first (upstream), then the cubebox backend + frontend land
together (they are tightly coupled through the `injected_message` event and the
cancel endpoint). Revisit at plan time.
