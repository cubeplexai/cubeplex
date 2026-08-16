# Run status UX — design

**Status:** Draft
**Date:** 2026-08-16
**Branch:** `feat/2026-08-16-run-status-ux`

## Goal

Show cancel, disconnect/reconnect, successful completion, and model failure
as one run-level status on the existing Info chip — and stop treating a
dropped browser connection or a user stop as "Reply failed".

## Context

A cubeplex run lives on the server. The browser only subscribes to its SSE
stream. Refresh already recovers an in-flight run (`bootstrap.active_run` →
`GET .../runs/{id}/stream`). Two client paths ignore that:

1. **Network drop mid-send.** `streamMessages` synthesizes
   `{ type: 'error', data: { message: 'Connection lost' } }`. `send()` treats
   every `error` as a dead run: it writes an assistant with
   `stop_reason: 'error'`, paints "Reply failed / Connection lost", and
   unlocks the composer. The worker keeps going. Refresh shows the truth.
2. **User hits Stop.** `cancelStream` already snapshots the partial turn as
   `stop_reason: 'aborted'`. The backend cancel path also publishes
   `_append_error(..., "Run cancelled")` as `internal_error`. If that event
   arrives before the client aborts SSE, the same error path paints
   "Reply failed / Run cancelled" plus
   "Something went wrong. Open this run's details…".

Those outcomes also render in three places: the assistant bubble, the list
footer `RunErrorBubble`, and the yellow `incompletePreviousAnswer` banner.
The Info chip under a completed turn only shows `run_id`.

## Approaches considered

| Approach | Pros | Cons |
|---|---|---|
| **A. Copy-only** — reword Connection lost / Run cancelled | Tiny | Composer still goes idle; user can send while the old run is live; stop still looks like a crash |
| **B. Soft disconnect + Retry button; cancel is quiet** | Honest | Wi-Fi blip needs a click; still two surfaces unless we also move errors |
| **C. Auto-reconnect + cancel is not an error + one status chip** (chosen) | Matches refresh recovery; one place for run outcome; composer stays locked while the run is alive | Need to split transport loss from run failure |

**Chosen: C.** The run is the source of truth. The chip is the single
user-facing status. Color is a secondary cue next to a short label.

## Design

### 1. What is a run failure

A run is failed only when the **server** says so: an SSE `error` that came
from the worker (classified `error_code`), or bootstrap `last_run_error` /
an assistant with `stop_reason: 'error'`.

These are **not** run failures:

- Fetch / SSE throw (`TypeError`, dropped socket, proxy reset)
- A client-synthesized `{ message: 'Connection lost' }` event — this event
  goes away
- User cancel (`POST .../cancel`). cubepi already persists
  `stop_reason: 'aborted'`. Do not publish an `internal_error` for that
- Aborting the local `AbortController` (navigate away, new send, Stop)

`ErrorCode` does **not** gain `run_cancelled`. Cancel is a status, not a
classified error (same decision as the 2026-06-04 error-UX plan).

### 2. Disconnect: stay subscribed

When the live consumer loses the socket **after** it has a `run_id`:

- Do not finalize the turn as `error`
- Keep `isStreaming`, `currentRunId`, `streamAgents`, and the Stop button
- Set a per-conversation transport state: `reconnecting`
- Re-attach with the existing `GET /runs/{id}/stream` and `Last-Event-ID`
  (same path refresh uses)
- Backoff like `useUserEvents`: 1s, doubling to 30s. Successful event
  resets. `online` and tab `visibilitychange → visible` retry immediately
- If the GET 404s or bootstrap says there is no `active_run`, read
  history / `last_run_error` / `last_run_status` and settle:
  completed, failed, stopped, or incomplete (stale worker)

If the POST never returned a `run_id`, there is nothing to resume. That is
a send failure: keep the optimistic user bubble, do not claim the model
failed, and let the user send again.

A clean server close without `done` / `error` is treated as disconnect, not
success.

### 3. Cancel: not an error

Backend: both `CancelledError` handlers in `run_manager` keep
`status=cancelled` and the dangling-tool repair. They **stop** calling
`_append_error(..., "Run cancelled")`. They do not write
`set_conversation_last_error`.

Frontend: while `cancellingConversationIds[conv]` is set, ignore inbound
`error` events (defense if an old worker still emits one). `cancelStream`
still snapshots partial content as `aborted`. After idle + reconcile, the
chip reads `stop_reason === 'aborted'`.

The "Cancelling run…" banner goes away; the chip says **Stopping…** then
**Stopped**.

### 4. One chip, one popover

Upgrade `RunInfoChip` to a run-status chip. Same slot under the turn
(copy / tokens / fork / time). Popover is the only detail drawer.

| State | Chip (closed) | Always visible? | Popover | Color |
|---|---|---|---|---|
| Completed | current muted Info icon | No — hover row, as today | Run ID + copy | none |
| Stopping | **Stopping…** | Yes | "Stopping this response" + run id | muted |
| Stopped | **Stopped** | Yes | "You stopped this response" + run id | muted |
| Reconnecting | **Reconnecting…** | Yes | "Connection dropped. The run is still going." + run id | warning |
| Disconnected (still retrying / offline) | **Connection lost** | Yes | Same + **Retry** (forces one attach now) | warning |
| Failed | **Reply failed** | Yes | Classified `runError.*` copy + expandable raw `details` + run id | danger |
| Incomplete (`last_run_status === 'stale'`) | **Incomplete** | Yes | Current "previous response was incomplete" copy + run id | warning |

Success stays silent: no green "Completed" badge.

Color never stands alone. The visible label is the signal; `aria-label` is
a full sentence ("This run was stopped", "Reconnecting to this run").

**Streaming turn:** the same chip renders on the in-flight footer (next to
the bouncing dots), using `currentRunId`. Do not wait for a history
assistant row.

**History turn:** derive from that message's `stop_reason`, plus
`last_run_error` when `run_id` matches (classified failure copy), plus
`lastRunStatus === 'stale'` on the conversation's latest run.

### 5. Surfaces that go away

Remove these once the chip owns the copy:

- Assistant bubble "Reply failed" + `error_message` block
  (`AssistantMessage` `showErrorBubble`)
- List-footer `RunErrorBubble`
- Yellow `incompletePreviousAnswer` banner
- The standalone "Cancelling run…" status row

Partial generated text stays in the transcript. Status, reason, retry, and
run id live only in the chip / popover.

The `runError.*` i18n strings stay. They move into the failed popover.
"Open this run's details" now means this popover, not a missing page.

### 6. Composer

While `reconnecting` / `disconnected` with a live `run_id`, the composer
behaves as if still streaming: Stop works, text steers, attachments stay
blocked. Do not return to a fresh Send that would 409 / auto-steer.

After **Stopped**, **Failed**, **Incomplete**, or **Completed**, the
composer is idle as today.

## Out of scope

- Multi-tab / multi-device live sync of transport state
- Changing Redis event retention or the replay coalescer
- Adding `run_cancelled` to `ErrorCode`
- Admin traces UI
- IM / scheduled-task / trigger surfaces (they are not this chip)
- A green success badge

## Success criteria

1. Unplug the network mid-reply: no "Reply failed". Chip shows
   Reconnecting / Connection lost. Composer still has Stop. Plug back in
   (or wait for backoff): tokens resume without refresh.
2. Refresh while disconnected, then reconnect: same as today — bootstrap
   replays and tails the active run.
3. Click Stop: chip goes Stopping → Stopped. No red "Reply failed", no
   "Something went wrong". Partial text remains. Reload still shows
   `stop_reason: aborted` and the Stopped chip.
4. A real provider/tool error: chip says Reply failed; popover shows the
   classified sentence and raw details; run id is still copyable.
5. A stale worker (`last_run_status: stale`): Incomplete on the chip, not
   a separate yellow banner.
6. A completed turn still hover-reveals the muted Info chip with only the
   run id.
