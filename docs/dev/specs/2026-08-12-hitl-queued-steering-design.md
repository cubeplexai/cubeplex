# Queue steering while HITL is pending

- **Date:** 2026-08-12
- **Status:** Design, pending review
- **Area:** conversation composer, steering API, run manager, HITL resume
- **Related:** issue #488,
  [durable HITL design](2026-06-02-hitl-checkpointed-respond-design.md),
  [agent system design](../../../backend/docs/agent-system-design.md),
  [prompt-cache discipline](../../../backend/docs/prompt-cache-discipline.md)

## Decision summary

Keep the composer available while a run is waiting for HITL or resuming from
an HITL answer. Text entered in that period is steering for the existing run,
not a new turn and not an answer to the HITL form.

Make steering submitted while an HITL request is pending server-owned and
durable. `POST /steer` stores those messages in Postgres before acknowledging
them, then the worker that resumes the Agent copies them into CubePi's steering
queue. Ordinary live steering keeps its current direct delivery path. The
durable path covers the gap from `paused_hitl` through resume-worker startup
without changing the normal running path.

CubePi already supplies the required ordering on resume:

```text
assistant tool_use
→ HITL tool_result
→ queued steering UserMessage(s), persisted order
→ next model call
```

CubePlex must not insert a queued message directly into `cubepi_messages`.
It passes the message through `agent.steer(...)`; CubePi emits and checkpoints
the `UserMessage` at its next safe point. This preserves tool-use/tool-result
adjacency, replay fidelity, and the existing `injected_message` event contract.

## Goal

Let users continue giving direction during a durable HITL pause without
starting a second turn, losing their text, or breaking Agent history order.

## Problem

The frontend currently treats `isStreaming` as both:

1. "the assistant is actively producing work", and
2. "this conversation owns an active run, so composer text is steering".

Those meanings diverge during durable HITL. A paused run has no live worker,
so `isStreaming` is false even though the conversation still owns a run. The
composer is therefore locked. If it is unlocked without changing the routing
contract, it calls the new-turn send endpoint and receives 409. The same race
exists immediately after an HITL form is submitted: Redis already reports the
run as `running`, but the resume worker may not have built and registered its
Agent yet, so the current Redis Pub/Sub-only steering path can lose a message.

The current pending-steer chips are also client-only. A refresh, another tab,
or a worker restart can forget text that the UI appeared to accept.

## Goals

- The textarea remains editable while an `ask_user` or sandbox-confirm HITL
  request is waiting, submitting, approving, denying, or being cancelled via
  the existing cancel-as-answer flow.
- Text submitted in those states is attached to the existing run as steering.
- A successful steering response means the server has durably accepted the
  text. Refreshes, long HITL pauses, and worker replacement do not lose it.
- Steering submitted before the resume safe point is visible to the model
  after the HITL tool result and before the next model call.
- Messages committed before one drain begins are handed to CubePi in
  `(created_at, id)` order and retain group-chat sender identity. Durable
  enqueues for one conversation are transaction-serialized; simultaneous
  clients are ordered by server lock acquisition, not client timestamps.
- Cancelling a not-yet-injected steering message works while HITL is paused.
- The normal live-steering experience and `injected_message` transcript
  behavior remain the same.
- A stale frontend state does not turn a text-only composer submission into a
  visible 409; the client refreshes lifecycle state and retries once as
  steering when the server reports an active run.

## Non-goals

- Treating composer text as an implicit answer to the HITL form. The user must
  still answer, approve, deny, or cancel the card explicitly.
- Steering with attachments. Attachments remain disabled while any run is
  active; queued steering is text-only in this version.
- Starting a second concurrent run for the conversation.
- Reordering a steering message ahead of the HITL tool result.
- Adding a general offline outbox for ordinary idle-conversation messages.
- Persisting ordinary live steering when no HITL request is pending.
- Retrying an HITL resume after the run reaches `errored`; safe recovery from
  partial tool or provider side effects requires a separate design.
- Real-time synchronization of pending chips across multiple open tabs. A
  bootstrap or focus refresh is authoritative; injected transcript messages
  continue to arrive through the run stream.

## Options considered

### A. Unlock the composer and keep the queue in the browser

The browser could retain text locally until the HITL answer succeeds, then
call the existing live steering endpoint.

This is small, but it loses accepted-looking text on refresh, cannot hand the
message to a different worker, and still races Agent construction after the
answer POST. It also makes each tab a different source of truth.

**Rejected.**

### B. Store paused steering in Redis and hand it to the resume worker

Redis is already used for run metadata and events, so a run-scoped list would
be straightforward.

The durable HITL request is intentionally able to outlive Redis run-data TTLs.
A steering message typed during that pause must have the same durability; an
expiring Redis list would silently lose user-authored content. Redis Pub/Sub is
also only a wake-up signal and cannot be the delivery record.

**Rejected as the source of truth.** Redis remains useful as a low-latency
notification to the owning worker.

### C. Persist steering accepted while HITL is pending

Use a scoped Postgres queue while the durable HITL request exists. The API
commits the queue row first. The owning worker is notified through a new Redis
control message, and a database-backed delivery coordinator claims the row and
calls `agent.steer(...)`. Agent registration and a bounded fallback poll also
drain accepted rows, so Pub/Sub loss is recoverable.

Once the pending HITL request is cleared, `POST /steer` uses the existing live
delivery path. This keeps the new table and delivery state machine scoped to
the durability gap that prompted the feature.

**Recommended.**

## Product behavior

### Waiting for HITL

- The HITL card remains visible and actionable.
- The textarea is enabled. Its hint explains that text will be sent after the
  HITL decision; sending text does not submit the card.
- Enter snapshots the submitted text into a local `submitting` chip and clears
  the textarea immediately, leaving it ready for more text. If the queue API
  rejects the message, the snapshot is prepended to any newer draft instead of
  replacing it; text typed while the request was in flight is never cleared.
- The chip uses a queued label while no Agent is live and remains cancellable.
- The attachment button stays disabled because steering is text-only.

### Submitting an HITL answer

- The form disables its own controls as needed to prevent duplicate answers,
  but it does not disable the conversation textarea.
- The frontend models this period as `resuming_hitl`, distinct from both
  `paused_hitl` and active assistant streaming.
- Text sent in this period goes to the same durable steering queue. It does
  not depend on whether the resume worker has registered the Agent yet.

The persisted Redis run status does not need a new value for correctness.
`resuming_hitl` is a frontend lifecycle derived from a locally submitting HITL
card or from bootstrap returning both a running active run and the still-
persisted pending request. Delivery routing uses the durable queue, not this
presentation state.

### Approve, deny, and cancel-as-answer

Approving or denying a sandbox command and answering `ask_user` all use the
same rule: queued steering is injected after that HITL tool result.

The current HITL Cancel action is a cancel-flavoured answer that resumes the
Agent so it can respond contextually; it is not a hard deletion of the run.
Queued text follows the cancel tool result. This is useful when the text says
what the user wanted instead of the cancelled question.

A true hard Stop remains a separate lifecycle. Once hard stopping has begun,
the composer is locked because the run is no longer expected to consume new
steering.

### Injection

When CubePi reaches a safe point, the pending chip becomes a normal user
message at the position reported by the existing `injected_message` event.
It is not rendered in the transcript earlier because that would disagree with
checkpoint history after reload.

One drain may run for a given run at a time. It hands all rows committed before
that drain begins to `agent.steer(...)` in `(created_at, id)` order. A request
that commits after the batch was claimed joins the next batch. Concurrent
durable enqueues are serialized on the conversation row, so this is a stable
server acceptance order. CubePi preserves the order in which the coordinator
hands it messages.

## Frontend lifecycle model

Add a per-conversation run lifecycle independent from `isStreaming`:

| Lifecycle | Meaning | Composer submit |
|---|---|---|
| `idle` | No active or pending run | Start a new turn |
| `running` | Agent is live, with no pending HITL | Send live steering |
| `paused_hitl` | Pending HITL, no live Agent | Queue steering |
| `resuming_hitl` | HITL answer accepted or being processed | Queue steering |
| `stopping` | Hard Stop is in progress | Disabled |

`isStreaming` remains a rendering signal for assistant output, typing
indicators, and the live Stop button. It is no longer the routing authority for
composer text.

`messageStore.steer(...)` accepts `running`, `paused_hitl`, and
`resuming_hitl`. Bootstrap hydrates both the lifecycle and `pendingSteers`.
The HITL pending slots continue to own card rendering.

The pending-steer client shape gains server state:

```ts
type PendingSteer = {
  steerId: string
  text: string
  state: 'queued' | 'dispatched' | 'failed'
  createdAt: string
}
```

The source tab adds an optimistic chip while the request is in flight. If the
API does not durably accept it, an empty draft becomes the submitted snapshot;
otherwise the snapshot is prepended to the newer draft with a newline. An
`injected_message` event is terminal for its
`steer_id`: a later POST response must not recreate a chip that the event
already removed.

### Stale-client recovery

A tab can believe the conversation is idle while another tab or worker has
already paused the run. If a text-only new-turn POST returns
`active_run_conflict`, the store performs one bootstrap refresh. When that
refresh finds `running`, `paused_hitl`, or `resuming_hitl`, it retries the same
text once through `/steer` with one stable `steer_id`.

Attachments are not auto-converted to steering. An attachment-bearing conflict
keeps the draft and attachments intact and explains that the active run must
finish first.

## Durable queue data model

Add an org/workspace-scoped `steering_messages` business table with a new
public ID prefix `stm`.

| Field | Purpose |
|---|---|
| `id` | Public `stm-...` primary key |
| `org_id`, `workspace_id` | Required scope columns |
| `conversation_id`, `run_id` | Owning conversation and logical run |
| `client_steer_id` | Stable client id; unique with `conversation_id` |
| `content` | Accepted text |
| `sender_user_id` | User authorized at enqueue time |
| `sender_display_name` | Group-chat display snapshot |
| `hitl_question_id` | Required pending HITL question at enqueue time |
| `state` | `queued`, `dispatched`, `cancel_requested`, `injected`, `cancelled`, or `failed` |
| `delivery_owner`, `delivery_lease_until` | Crash-recoverable delivery claim |
| `created_at`, `updated_at` | Timezone-aware timestamps |

Constraints and indexes:

- Unique `(conversation_id, client_steer_id)` keeps a delayed retry from being
  delivered to a later run in the same conversation.
- Index `(run_id, state, created_at, id)` supports ordered claim and drain.
- The foreign key uses `ON DELETE CASCADE` for physical deletion. Because the
  user-facing conversation delete is soft, that path explicitly removes queue
  rows in the same transaction.
- Repository methods always filter by `(org_id, workspace_id)`; routes retain
  the existing workspace member dependency and do not share admin handlers.
- Durable enqueue locks the scoped conversation row before checking limits and
  inserting. This makes capacity enforcement and accepted order atomic across
  API workers and serializes correctly with soft deletion.

The API enforces these bounds before enqueue and counts UTF-8 bytes, not
JavaScript or Python characters:

- one message: at most 32 KiB;
- one run: at most 32 active rows and 256 KiB of active content;
- one conversation: at most 100 visible unresolved rows and 1 MiB of their
  content, including rows in `failed` state.

These limits also bound `bootstrap.pending_steers`. A 413
`steer_content_too_large` or 429 `steer_queue_full` response does not create a
row, and the frontend keeps the submitted text in the composer.

The migration must be generated with Alembic autogenerate. `injected` and
`cancelled` tombstones remain for a 24-hour idempotency window after terminal
transition; a bounded cleanup deletes older tombstones. Normal completion,
hard Stop, unrecoverable error, and forced HITL cancellation call shared
run-finalization logic. A bounded reconciliation scan covers stale-run
transitions from startup recovery, bootstrap, stream reads, and the IM
run-claim path. Both change uninjected rows to `failed`. Moving failed text to
the draft or dismissing it changes the row to `cancelled` instead of deleting
the idempotency record. Failed text otherwise remains visible; user-authored
text is never silently discarded. Soft conversation deletion removes all of
its queue rows immediately.

## Queue state machine

```text
                 worker claim
queued ------------------------------> dispatched
  |                                        | \
  | cancel                                 |  \ cancel
  v                                        v   v
cancelled                             injected  cancel_requested
                                                  |          |
                                                  | removed  | injection won
                                                  v          v
                                              cancelled   injected

queued/dispatched -- run ends first --> failed
failed -- move to draft or dismiss --> cancelled
injected/cancelled -- after 24 hours --> deleted
```

- `queued` means committed to Postgres but not copied into an Agent queue.
- `dispatched` means an owning worker has claimed it for the in-memory CubePi
  steering queue. It remains present in bootstrap until injection.
- `cancel_requested` prevents a dispatched item from being reclaimed while
  the owning worker resolves `agent.cancel_steer(...)` versus injection.
- `injected` is acknowledged only after CubePi emits
  `MessageEndEvent(UserMessage)` and the checkpointer has persisted it.
- An expired delivery lease can be reclaimed only by a new worker that owns
  the run; the current owner never re-dispatches its own staged row.
- Before reclaim, the coordinator reconciles `client_steer_id` against
  checkpointed user-message metadata. If the message is already in history,
  it marks the row `injected` instead of injecting a duplicate.

## Delivery coordinator

The run manager owns a process-level steering delivery coordinator:

1. `POST /steer` commits an idempotent `queued` row.
2. It publishes a Redis `steer_available` control message containing only the
   run and queue item IDs. Redis is a wake-up hint, not the payload authority.
3. The worker that has `self._agents[run_id]` enters a per-run asynchronous
   single-flight section shared by registration, Pub/Sub, and fallback-poll
   drains. It claims committed rows for that run with
   `FOR UPDATE SKIP LOCKED`, ordered by `(created_at, id)`, and commits
   `dispatched` with a short lease.
4. It calls `agent.steer(UserMessage(...))` with `client_steer_id` and sender
   metadata. A synchronous delivery failure returns the row to `queued`.
5. When the converted CubePi event becomes `injected_message`, the async event
   drainer attempts to mark the matching row `injected`, then publishes the
   event. An acknowledgement failure never suppresses the user-facing event;
   history reconciliation repairs the row later.

The coordinator drains once synchronously after an Agent is registered and
before `agent.respond(...)` begins. It also wakes on the control notification.
One bounded process-level fallback poll queries queued rows every two seconds
only for run IDs owned by that process; this recovers a missed Pub/Sub
notification without one poller per run. All three entries use the same
per-run lock, so overlapping wake-ups cannot reverse two claimed batches.

This startup drain closes the current HITL gap: steering accepted while the
run is paused or while the resume worker is being constructed is already in
the Agent queue when `agent.respond(...)` begins.

Delivery claims are short leases, not permanent ownership. Worker death leaves
the database row recoverable. No message content is included in Redis control
payloads or logs.

## CubePi ordering and prompt history

No CubePi change is required. `run_agent_loop_resume(...)` already:

1. executes and checkpoints the pending HITL tool result,
2. clears the pending HITL request,
3. drains steering messages,
4. proceeds to termination checks or the next model turn.

The order is important for Anthropic-compatible providers: a user message
must not appear between an assistant `tool_use` and its `tool_result`.

Queued steering is a normal tail `UserMessage`, with `steer_id` in metadata.
It does not change the system prompt, tool registration order, middleware
order, or any other stable prompt prefix. Prompt-cache behavior therefore
matches ordinary live steering.

## API contracts

### `POST /conversations/{conversation_id}/steer`

Retain the request:

```json
{
  "content": "Use the smaller dataset instead",
  "steer_id": "steer-..."
}
```

Resolve delivery in this order:

1. After validating the request, look up `(conversation_id, steer_id)` in the
   durable queue. If content and sender match, return its current state and
   original `run_id` without dispatching again. Reusing the ID with different
   content or a different sender returns 409 `steer_id_conflict`.
2. If a checkpointed `pending_request` and matching `pending_run_id` exist,
   enqueue durably for that HITL run. This covers `paused_hitl`, resume-worker
   startup, and a long pause whose Redis keys expired.
3. Otherwise, if Redis has the same conversation's `running` run, use the
   existing live `dispatch_steer` path and its `steered`/`published` response.
4. Otherwise return `no_active_run`.

For durable enqueue, return the current stored state, including on an
idempotent retry:

```json
{
  "status": "queued",
  "run_id": "...",
  "steer_id": "steer-..."
}
```

`status` is one of `queued`, `dispatched`, `injected`, `cancelled`, or `failed`.
`queued` and `dispatched` keep a pending chip. `injected` is already owned by
history and must not create a chip; if that history message is not in the local
transcript yet, the client refreshes bootstrap once. `cancelled` and `failed`
keep the submitted text in user control and require a new `steer_id` for any
later submission. Ordinary live steering retains `steered`, `published`, and
`no_active_run`.

`paused_hitl` no longer returns 409. `no_active_run` remains a non-error result
when no run or persisted HITL request exists before insertion. If a run ends
after a row commits, the route returns that row as `failed`, so accepted text
remains recoverable. The client restores the draft for `no_active_run`.

### `POST /conversations/{conversation_id}/steer/cancel`

The endpoint also accepts paused/resuming runs. It transitions a `queued` row
directly to `cancelled`. For `dispatched`, it atomically changes the row to
`cancel_requested`, notifies the owning worker, and returns `accepted`. The
worker commits `cancelled` when `agent.cancel_steer(client_steer_id)` removes
the message. If injection already won, it commits `injected` instead and the
history event remains authoritative. A replacement owner reconciles an
expired `cancel_requested` row against history; absent a persisted message, it
finishes the cancellation without dispatching the item.

### `GET /conversations/{conversation_id}/bootstrap`

Add:

```json
{
  "pending_steers": [
    {
      "steer_id": "steer-...",
      "content": "Use the smaller dataset instead",
      "state": "queued",
      "created_at": "2026-08-12T12:00:00Z"
    }
  ]
}
```

Return only rows visible to the current workspace member. `queued`,
`dispatched`, and `failed` rows are returned; injected/cancelled tombstones are
not rendered.

### SSE

Keep `injected_message` unchanged. No new event is required for correctness:
the POST response updates the source tab, bootstrap restores durable state,
and `injected_message` commits the final transcript position. Live cross-tab
pending-chip synchronization is out of scope for this version. The client
records the event's `steer_id` before removing its chip; any later enqueue
response for that ID is ignored for pending-state purposes.

## Failure and race behavior

| Case | Required result |
|---|---|
| Steer races HITL answer submit | One durable row; startup drain or live coordinator delivers it once |
| Duplicate POST retry within 24 hours | Return the existing row's actual state and original run; no duplicate injection |
| Same `steer_id` with different content or sender | Return 409 `steer_id_conflict`; do not mutate or dispatch |
| `injected_message` arrives before POST response | Transcript wins; the response cannot recreate the pending chip |
| Page refresh while paused | Bootstrap restores HITL card and pending steer chips |
| Worker dies before dispatch | Row remains `queued` or its lease expires; next owner reclaims |
| Worker dies after checkpoint, before queue ack | History reconciliation marks `injected`; no duplicate |
| HITL answer POST is rejected | UI refreshes bootstrap and returns to `paused_hitl`; queued steering remains queued |
| Resume worker reaches `errored` | Do not retry `agent.respond`; queued/dispatched rows become visible `failed` rows |
| Run ends before injection | Row becomes `failed`; UI offers retry as a new turn or dismiss |
| User cancels queued steer | It never reaches CubePi |
| Cancel races injection | Exactly one terminal outcome; persisted history wins once injected |
| Redis run keys expire during long pause | `pending_run_id` resolves the run; Postgres queue remains intact |

## Security and privacy

- Reuse workspace-scoped conversation membership checks before enqueue,
  cancel, and bootstrap reads.
- Store the sender identity that was authorized when the message was accepted.
  A later membership change does not rewrite message authorship.
- Do not log steering content or publish it on Redis control channels.
- Enforce the explicit byte and queue bounds defined in the data model before
  returning a durable success response.
- User-facing soft deletion explicitly deletes queue rows. Physical
  conversation deletion uses the foreign-key cascade; workspace/user deletion
  paths include the new scoped table through normal model relationships.

## Test strategy

Tests protect the ordering and durability contracts, not DOM presence.

### Backend e2e

Use real Postgres, Redis, FastAPI, and the CubePi checkpointer. Mock only the
outer model response where deterministic tool calls are needed.

1. Pause on `ask_user`, enqueue two steering messages, submit the answer, and
   assert checkpoint history order is tool result → steer 1 → steer 2 → next
   assistant response.
2. Repeat for sandbox approve and deny.
3. Race enqueue against resume-worker registration with a synchronization
   barrier; assert exactly one user message with that `steer_id` is persisted.
4. Queue with no live worker, construct a new run manager, resume, and assert
   delivery from the durable row.
5. Expire Redis run keys during a pending HITL, enqueue using checkpointed
   `pending_run_id`, refresh bootstrap, and resume successfully.
6. Cancel a queued steer before resume and assert it never appears in history.
7. Simulate checkpoint success before queue acknowledgement, reclaim the
   expired lease, and assert history reconciliation prevents duplication.
8. Assert cross-workspace enqueue, cancel, and bootstrap access return scoped
   404 behavior.
9. Race registration, Pub/Sub, and fallback-poll drains for two rows and assert
   only one drain runs for the run and CubePi receives the committed batch in
   `(created_at, id)` order.
10. Exercise every stale-run entry point and soft conversation deletion; each
    path finalizes or removes the scoped rows instead of leaving an active row.
11. Assert the per-message, per-run, and per-conversation byte/count limits
    return their stable errors without inserting a row.

### Backend unit tests

- Queue transition and lease-claim functions.
- Idempotent `(conversation_id, client_steer_id)` enqueue and mismatched-retry
  rejection.
- Terminal cleanup classification (uninjected → `failed`; terminal tombstones
  retained for 24 hours and then purged in bounded batches).
- Conversion of queue rows to CubePi `UserMessage` metadata.

### Frontend store and flow tests

- `paused_hitl` and `resuming_hitl` composer submissions call `steer`, not
  new-turn `send`.
- Accepted rows remain as chips through bootstrap and become transcript
  messages only on `injected_message`.
- Submit clears the captured snapshot immediately. A rejected enqueue restores
  it before any text typed in flight instead of overwriting that newer text.
- An `injected_message` received before the enqueue response remains terminal
  and the response does not recreate its chip.
- A text-only `active_run_conflict` refreshes bootstrap and retries exactly
  once as steering; attachments are retained and not retried.
- HITL form submission state does not lock the textarea, while hard Stop does.
- A rejected HITL answer refreshes bootstrap and returns the lifecycle to
  `paused_hitl` instead of leaving it in `resuming_hitl`.

One deterministic Playwright business-flow test should cover: the test model
emits a fixed `ask_user` call → the user queues text → the user submits the form
→ queued text appears in the transcript at the correct point → the fixed next
model response observes the expected request history. This does not depend on
a real model deciding whether to call the HITL tool.

## Documentation and rollout

This is a conditional cutover inside the existing steering endpoint: a
persisted HITL request uses the durable queue, while an ordinary running run
keeps the current live dispatch. The frontend lifecycle and backend routing
ship together; no dual-write or compatibility shim is needed.

The implementation PR must update the conversation guide to explain:

- text can be queued while an HITL card is waiting,
- queued text does not answer the card,
- attachments remain unavailable until the run finishes.

Operational signals:

- count and age of `queued`/`dispatched` steering rows,
- enqueue-to-injection latency,
- expired delivery leases and reconciliation outcomes,
- runs ending with failed steering rows,
- count and age of terminal tombstones awaiting 24-hour cleanup.

## Success criteria

- Typing and sending text before or during HITL form submission never produces
  the current paused-HITL 409 in normal use.
- A successful queue response survives refresh, Redis expiry, and worker
  replacement.
- Every accepted steering message is injected once, cancelled, or surfaced as
  failed; none disappear silently.
- Persisted history always places HITL tool results before queued user
  messages.
- Normal live steering remains responsive and preserves its existing
  transcript behavior.
