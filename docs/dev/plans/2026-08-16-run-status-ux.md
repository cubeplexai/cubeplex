# Run status UX — implementation plan

**Goal:** Stop painting transport loss and user cancel as "Reply failed";
auto-reattach a live run; put every run outcome on the Info chip.

**Architecture:** The worker is still the source of truth. The client gains
an explicit transport state (`connected` / `reconnecting` / `disconnected`)
separate from run lifecycle. `send` / `consumeRunStream` treat a dropped
socket as "reattach via `streamRun` + `Last-Event-ID`", not as an `error`
event. Cancel stops publishing `internal_error`. `RunInfoChip` becomes the
only status surface; the three existing failure banners go away.

**Tech stack:** FastAPI `run_manager` · Zustand `messageStore` · Next.js
chat chips · next-intl · Vitest (store + chip) · one Playwright client
state-machine check if a unit test cannot express reconnect-while-send.

---

## Unit 1 — Cancel is not an error event

**Files**

- `backend/cubeplex/streams/run_manager.py` — both `CancelledError`
  handlers (prompt path and respond path). Keep `status=cancelled` and
  dangling-tool repair. Delete the `_append_error(..., "Run cancelled")`
  calls. Do not call `set_conversation_last_error`.
- Existing cancel / stream tests under `backend/tests/` that assert an
  error event with message `"Run cancelled"` — update them to assert the
  stream has no `error` event on cancel (or only `status=cancelled` on
  meta).

**Interfaces**

- SSE contract: user cancel ends the stream without a type=`error` event.
  Meta `status` remains `"cancelled"`.
- `ErrorCode` unchanged.

**Core logic**

```
except CancelledError:
    update_run_meta(status="cancelled")
    repair dangling tool_calls
    # no ErrorEvent
    raise
```

If a late `error` still arrives (old worker), Unit 2 ignores it while
cancelling.

**Tests**

- Backend unit or existing stream test: cancelling an in-flight run does
  not append an event with `type=error` / message `"Run cancelled"`.
- Do not add `run_cancelled` to the enum.

---

## Unit 2 — Transport loss ≠ run failure; reattach

**Files**

- `frontend/packages/core/src/api/stream.ts` — `streamMessages` catch:
  do not yield a fake `{ type: 'error', message: 'Connection lost' }`.
  Re-throw (except `AbortError`, which stays silent).
- `frontend/packages/core/src/api/runStreams.ts` — `streamRun` already
  throws on network failure; keep that.
- `frontend/packages/core/src/stores/messageStore.ts` —
  `send`, `consumeRunStream`, `loadMessages`:
  - Add `streamConnection: Record<convId, 'connected' | 'reconnecting' | 'disconnected'>`
    (or a single field on the active stream — one live SSE today).
  - On socket drop after `currentRunId` is set: keep streaming flags,
    mark `reconnecting`, loop `streamRun(..., lastAppliedEventId)` with
    the same backoff as `useUserEvents` (1s → 30s).
  - `online` + `visibilitychange=visible` reset backoff and retry now.
  - `AbortError` / `cancellingConversationIds` / ownership loss: stop
    the loop, do not mark failed.
  - While cancelling: ignore `event.type === 'error'`.
  - SSE `error` from the server still terminals as today (classified
    failure).
  - Socket drop **before** `run_id`: leave composer idle, do not write
    `stop_reason: 'error'` / "Connection lost".
  - `consumeRunStream` catch today sets `errors[conv]=internal_error`
    and keeps `isStreaming`. Change: same reattach loop, no error seed.
- Tests: `frontend/packages/core/__tests__/stores/messageStore*.ts`,
  `frontend/packages/web/__tests__/hooks/useMessages.test.ts`
  (the existing "reconnect fails mid-run" case currently expects
  `internal_error` + `isStreaming: true` — flip to reconnecting / no
  error bubble).

**Interfaces**

```ts
// messageStore (sketch)
streamConnection: 'connected' | 'reconnecting' | 'disconnected' | null

// send / consume: after run_id is known
onTransportDrop:
  if cancelling or !owns(conv) or aborted: return
  streamConnection = 'reconnecting'
  backoff attach streamRun(client, conv, runId, lastAppliedEventId)
  on GET 404 or bootstrap.active_run == null: settle from history
```

`errors[conv]` is only set from a **server** `error` event or bootstrap
`last_run_error` / `active_run.error_code`. Never from `err.message`.

**Core logic**

- One reattach loop per owned run. A new `send` / `loadMessages`
  reattach / `clearStream` aborts it via the existing
  `activeStreamController`.
- Clean SSE end without `done`/`error` → same as drop (reattach).
- After settle from bootstrap: `done` in history → finalize completed;
  `stop_reason=aborted` → stopped; `last_run_error` → failed;
  `last_run_status=stale` → incomplete; else if no active run and no
  assistant → idle (run never produced tokens).

**Tests** (unit, in-process; mock `fetch`)

- Mid-send `fetch` reject after `onRunId`: `isStreaming` stays true,
  `currentRunId` stays, `errors[conv]` is null, `streamConnection` is
  reconnecting, next `streamRun` is called with the last event id.
- `AbortError` during send: no reconnect, no error bubble.
- Server `error` event: still `stop_reason: 'error'` + `errors[conv]`.
- Cancel flag set: inbound `error` does not flip the turn to failed.
- Bootstrap replay drop: no `internal_error` (replaces today's test).

---

## Unit 3 — Status chip is the only outcome surface

**Files**

- `frontend/packages/web/components/chat/RunInfoChip.tsx` — accept
  `status` + optional `ErrorEventData` + `onRetry`. Trigger label/color
  per spec table. Popover: status sentence, classified copy + raw
  details when failed, run id + copy, Retry when disconnected.
- `frontend/packages/web/components/chat/AssistantMessage.tsx` — delete
  `showErrorBubble`. Pass derived status into the chip on the history
  action row. On the streaming footer, render the same chip with
  `currentRunId`.
- `frontend/packages/web/components/chat/MessageList.tsx` — remove
  `RunErrorBubble`, `incompletePreviousAnswer` banner, and the
  "Cancelling run…" row. Derive chip props from store
  (`streamConnection`, `cancellingConversationIds`, `errors`,
  `lastRunStatus`, message `stop_reason`).
- `frontend/packages/web/components/chat/RunErrorBubble.tsx` — keep the
  i18n resolution helper (or move it next to the chip) so the popover
  can reuse classified copy; the standalone banner component can die if
  nothing else imports it.
- `frontend/packages/web/messages/en.json` + `zh.json` — chip labels
  (Stopping / Stopped / Reconnecting / Connection lost / Reply failed /
  Incomplete) and popover sentences. Reuse `runError.*` for failures.
- Tests: `RunInfoChip.test.tsx`, `AssistantMessage` / `MessageList`
  tests that currently look for the alert "Reply failed" or
  `role=alert` on `stop_reason=error`.

**Interfaces**

```ts
type RunChipStatus =
  | 'completed'
  | 'stopping'
  | 'stopped'
  | 'reconnecting'
  | 'disconnected'
  | 'failed'
  | 'incomplete'

function RunInfoChip(props: {
  runId: string | null | undefined
  status: RunChipStatus
  error?: ErrorEventData | null
  onRetry?: () => void
})
```

Derivation (UI layer, not a second store flag):

- live + cancelling → `stopping`
- live + reconnecting/disconnected → those
- history `stop_reason === 'aborted'` → `stopped`
- history `stop_reason === 'error'` or `errors[conv].runId === runId` →
  `failed`
- last run + `lastRunStatus === 'stale'` → `incomplete`
- else → `completed` (hidden Info)

Completed chip stays hover-only with the row. Every other status is
visible without hover (`opacity-100`).

**Core logic**

- Do not invent a persisted "display status" column. History uses
  cubepi `stop_reason` (`aborted` / `error` / `stop`) plus existing
  bootstrap error/stale fields.
- Failed popover: `t('runError.' + error_code)` with the same fallback
  `RunErrorBubble` uses today; `<pre>` for `details` / `message`.
- Retry calls `loadMessages(force)` (existing reattach) or a thin
  `reattachRun()` that Unit 2 exposes if `loadMessages` is too heavy.

**Tests**

- Chip: completed shows only the info button; failed/stopped/reconnect
  show the label; failed popover shows classified copy; missing
  `error_code` falls back to `message`.
- MessageList: `stop_reason=error` no longer renders a transcript
  `role=alert` titled Reply failed; the chip does.
- Cancel store test already expects `aborted` — add that the chip
  status would be `stopped` (or assert MessageList does not mount
  `RunErrorBubble`).

---

## Unit 4 — User-facing docs (same PR as Unit 3)

**Files**

- `docs/site/docs/guides/conversations/basics.md` — Per-message actions:
  Info becomes run status (run id always; Stopped / Failed / Incomplete
  when relevant). Steering and stopping: Stop is not a failure; network
  drop reconnects without "Reply failed"; Stop still locks the composer
  until the worker is idle.

No new docs page.

**Tests**

- None. Docs-only.

---

## Spec coverage

| Spec | Unit |
|---|---|
| Transport drop is not a run error; auto-reattach | 2 |
| No `run_id` yet → send failure, not model failure | 2 |
| Cancel does not emit `internal_error` | 1 |
| Ignore error while cancelling | 2 |
| Chip table + streaming footer | 3 |
| Remove three banners | 3 |
| Composer stays streaming while reconnecting | 2 + 3 |
| Docs | 4 |

## Implementation order

1 → 2 → 3+4. Unit 1 is safe alone. Unit 2 without 3 already stops the
false "Reply failed"; 3 makes the remaining states visible in one place.

## Out of scope (plan)

Same as the spec. No migration. No `ErrorCode` change. No IM/scheduler UI.
