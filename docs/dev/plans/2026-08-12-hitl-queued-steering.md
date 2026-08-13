# HITL queued steering implementation plan

- **Goal:** Keep the conversation composer usable during HITL and deliver every
  accepted text instruction to the existing run without the paused-HITL 409,
  message loss, or invalid prompt history.
- **Architecture:** A workspace-scoped Postgres queue becomes the source of
  truth for steering only while a durable HITL request is pending. The API
  commits those rows before acknowledging them; Redis wakes the resume worker;
  a per-run single-flight coordinator passes ordered rows through
  `agent.steer(...)`. Ordinary live steering keeps its current direct path. The
  frontend routes composer text by an explicit per-conversation run lifecycle
  rather than by streaming visuals.
- **Tech stack:** FastAPI, SQLModel/SQLAlchemy, Alembic, Postgres, Redis Pub/Sub,
  CubePi, Next.js/React, Zustand, Vitest, pytest, and Playwright.
- **Design:**
  [Queue steering while HITL is pending](../specs/2026-08-12-hitl-queued-steering-design.md)

## Delivery shape

The approved spec and this plan form the design-review artifact. Implementation
is one concern—durable steering across the HITL lifecycle—and should land as
one follow-up PR containing backend, frontend, tests, and the matching user
documentation. Intermediate commits may follow the units below, but the API
cutover and frontend lifecycle must be released together.

No CubePi package change is planned. CubePi already checkpoints the HITL tool
result before draining steering on `agent.respond(...)`.

## Unit 1 — Durable steering queue

### Files

- Create `backend/cubeplex/models/steering_message.py` for the queue row and
  state enum.
- Modify `backend/cubeplex/models/public_id.py` to add the `stm` public ID
  prefix.
- Modify `backend/cubeplex/models/__init__.py` to register and export the model
  so Alembic autogenerate sees it.
- Create `backend/cubeplex/repositories/steering_message.py` for scoped enqueue,
  ordered claims, cancellation transitions, acknowledgement, reconciliation,
  bootstrap reads, and run cleanup.
- Modify `backend/cubeplex/repositories/conversation.py` so a user-facing soft
  delete removes uninjected steering text in the same transaction. The foreign
  key still uses `ON DELETE CASCADE` for a later physical purge.
- Generate the migration with
  `uv run alembic revision --autogenerate -m "add steering messages"`; keep the
  generated revision under `backend/alembic/versions/` without hand-editing its
  schema operations.
- Modify `backend/tests/unit/test_public_id.py` for the new prefix.
- Create `backend/tests/e2e/test_steering_message_repository.py` for the real
  Postgres state machine and scope constraints.

### Interfaces

`SteeringMessageState` has these stored string values:

```text
queued
dispatched
cancel_requested
injected
cancelled
failed
```

`SteeringMessage` contains:

- `id` with prefix `stm`;
- `org_id`, `workspace_id`, `conversation_id`, and `run_id`;
- `client_steer_id`, `content`, `sender_user_id`, and optional
  `sender_display_name`;
- required `hitl_question_id` captured when enqueue happens;
- `state`, optional `delivery_owner`, and timezone-aware optional
  `delivery_lease_until`;
- inherited timezone-aware `created_at` and `updated_at`.

The table has a unique constraint on `(conversation_id, client_steer_id)`, a
foreign key to the conversation, the normal org/workspace scope index, and an
ordered delivery index on `(run_id, state, created_at, id)`.

The scoped repository exposes typed operations for these contracts:

- look up an existing row by scoped conversation and client steer ID before
  current-run routing;
- idempotently enqueue by `(conversation_id, client_steer_id)`, returning the
  existing row and its original run on a request retry;
- lock the scoped conversation row for durable enqueue before capacity checks
  and insert, serializing accepted order and soft deletion across API workers;
- list visible `queued`, `dispatched`, and `failed` rows for one conversation;
- count active rows/bytes per run and visible unresolved rows/bytes per
  conversation before enqueue;
- claim an ordered batch for one run with `FOR UPDATE SKIP LOCKED`, including
  an expired lease owned by a dead worker;
- return a synchronously failed delivery claim to `queued`;
- transition `queued` or dismissed `failed` to `cancelled`, and transition
  `dispatched` to `cancel_requested`;
- mark an owned row `cancelled`, `injected`, or `failed` with compare-and-set
  predicates so a cancellation/injection race has one winner;
- mark active rows failed when a logical run really terminates and delete
  injected/cancelled tombstones after the 24-hour idempotency window.

Repository methods participate in the caller's transaction. A route, delivery
claim, or cleanup operation commits all of its state transition at once rather
than relying on multiple per-row commits.

### Core logic

- Store status as a bounded string column and use `SteeringMessageState` at
  Python boundaries, matching existing queue models without introducing a
  PostgreSQL enum lifecycle.
- Treat `(created_at, id)` as the deterministic order among rows committed when
  a drain starts. The conversation-row lock serializes durable enqueues; its
  acquisition order, rather than a client timestamp, defines simultaneous
  submissions. Requests not committed when a drain starts join a later batch.
- Enforce 32 KiB per message, 32 active rows/256 KiB per run, and 100 visible
  unresolved rows/1 MiB per conversation. Count UTF-8 bytes. These caps include
  failed rows where the spec says so and guarantee bootstrap stays bounded.
- Keep injected/cancelled tombstones for 24 hours, including after logical run
  cleanup, so a delayed retry cannot target a later run in the conversation.
- Preserve `failed` rows and their content for bootstrap. Dismiss or move to
  draft changes them to `cancelled`; the bounded cleanup deletes that tombstone
  only after the 24-hour idempotency window.
- User-facing conversation deletion is soft and therefore does not trigger an
  FK cascade. Explicitly delete these transient rows before stamping
  `Conversation.deleted_at`; do not change retention for history, artifacts,
  billing, or attachments.

### Tests

- Unit: `stm` IDs have the expected shape and fit the existing 20-character
  column limit.
- Backend e2e: duplicate enqueue returns one row; ordered claims are stable; two
  claimers cannot own the same row; expired leases are reclaimable; each valid
  and invalid state transition is enforced.
- Backend e2e: a retry after the original HITL run terminates returns its
  tombstone for 24 hours and never dispatches into a newer run.
- Backend e2e: every byte/count boundary accepts the maximum, rejects the next
  enqueue without a row, and returns the specified 413 or 429 error code.
- Backend e2e: concurrent enqueues respect the cap, receive a stable server
  order, and cannot race a soft conversation deletion into an orphaned row.
- Backend e2e: org/workspace-scoped reads and mutations cannot see another
  workspace's rows.
- Backend e2e: soft conversation deletion removes uninjected steering rows,
  while physical deletion also satisfies the FK contract.

## Unit 2 — Delivery coordinator and RunManager integration

### Files

- Create `backend/cubeplex/streams/steering_delivery.py` for delivery claims,
  CubePi message construction, cancellation, history reconciliation, and the
  bounded fallback poll.
- Modify `backend/cubeplex/streams/run_manager.py` to add durable HITL queue
  notifications alongside the existing live-steer control message,
  attach/detach Agents to the coordinator, acknowledge injected messages, and
  finalize queue rows with the logical run.
- Create `backend/tests/unit/test_steering_delivery.py` for coordinator behavior
  with fake repositories and Agents.
- Update `backend/tests/unit/test_run_manager_steer.py`,
  `backend/tests/unit/test_run_control_pubsub.py`, and
  `backend/tests/unit/test_run_control_crossinstance.py` for the durable
  notification contract.

### Interfaces

`SteeringDeliveryCoordinator` is owned by one `RunManager` process and exposes:

```text
start() -> None
stop() -> None
notify_available(run_id, item_id) -> None
drain_run(run_id, agent, ctx) -> None
resolve_cancel(run_id, client_steer_id, agent, ctx) -> None
ack_injected(run_id, client_steer_id, ctx) -> None
finalize_run(run_id, ctx, terminal_status) -> None
reconcile_and_purge_rows(limit=100) -> None
```

Its constructor receives the async database session factory, Redis client and
control channel, a stable process owner ID, a callback into the RunManager's
live Agent registry, and the shared checkpointer factory. `ctx` is the existing
`RunContext`; delivery stores it beside each registered Agent so every queue
query still carries `org_id` and `workspace_id`.

Redis control payloads become content-free wake-ups:

```json
{"type":"steer_available","run_id":"...","item_id":"stm-..."}
{"type":"steer_cancel_requested","run_id":"...","steer_id":"steer-..."}
```

The existing `steer` and `cancel_steer` control messages remain the ordinary
live-run path. The two new content-free messages are used only for rows accepted
while an HITL request is pending. Hard-run cancellation stays unchanged.

### Core logic

1. After the respond path registers `self._agents[run_id]`, it synchronously
   drains durable HITL rows before calling `agent.respond(...)`. A fresh prompt
   has no pending HITL rows and keeps the existing live steering path.
2. Claiming commits `state=dispatched`, `delivery_owner`, and a short lease
   before calling the synchronous `agent.steer(...)`. If that call raises, the
   same owner returns the row to `queued`.
3. Registration, the control listener, and one process-level two-second
   fallback poll all enter the same per-run `asyncio.Lock` before claim and
   dispatch. The poll considers only run IDs in that process's Agent registry.
   Redis loss cannot strand a row, and overlapping wake-ups cannot reverse two
   claimed batches. Before a HITL detach, the same lock removes uncheckpointed
   messages from the old Agent and returns owned `dispatched` rows to `queued`;
   locks are removed only after that reconciliation.
4. The CubePi Agent checkpoints a `MessageEndEvent(UserMessage)` before calling
   CubePlex listeners. When StreamConverter produces `injected_message`, the
   run manager attempts `ack_injected` and then publishes the existing SSE
   event. A database acknowledgement failure is logged and reconciled later;
   it never hides the user-facing event.
5. Before reclaiming an expired `dispatched` row, load checkpoint history and
   search user-message metadata for the same `steer_id`. Existing history wins:
   mark the row injected instead of calling `agent.steer(...)` again.
6. A `cancel_requested` row is never dispatched. The owner calls
   `agent.cancel_steer(...)`; successful removal becomes `cancelled`, while a
   message already found in history becomes `injected`. A replacement owner
   finishes an expired cancellation after the same history reconciliation.
7. Run cleanup marks uninjected rows failed only when the logical run is
   terminal. `paused_hitl` leaves rows recoverable. Hard Stop, completed,
   cancelled, stale, and `errored` paths finalize them; this feature does not
   make `errored` HITL resumes retryable. Injected/cancelled tombstones remain
   for 24 hours.
8. `_force_cancel_hitl` finalizes rows belonging to the old run before a new
   run is allowed to claim the conversation.
9. At startup and every 30 seconds, a scan claims at most 100 active queue rows
   without a live owner. It checks Redis run status and the checkpointed pending
   request, then uses the same compare-and-set finalizer for stale, terminal,
   or errored runs. This covers stale transitions initiated by startup recovery,
   bootstrap, stream reads, and the IM run-claim path without adding database
   work to the Redis-only `mark_run_stale` helper. Each pass also deletes at
   most 100 terminal tombstones older than 24 hours.

`RunManager.start_control_listeners()` starts the coordinator's drain and
reconciliation tasks after its Redis listeners, and `stop_control_listeners()`
stops both tasks before closing those listeners. No second application-lifespan
owner is introduced.

### Tests

- Unit: queued rows are converted to CubePi `UserMessage` objects with
  `steer_id`, sender metadata, text, and persisted batch order intact.
- Unit: startup drain stages rows after Agent registration and before
  `agent.respond(...)`; the companion e2e test proves CubePi checkpoints the
  tool result before those staged messages. Fresh `prompt` calls do not query
  the HITL queue.
- Unit: concurrent registration, notification, and poll drains for one run are
  single-flight and preserve batch order.
- Unit: missed Pub/Sub is recovered by the bounded poll without creating a
  per-run polling task.
- Unit: a synchronous dispatch failure returns the row to queued; a failed
  acknowledgement still publishes `injected_message`.
- Unit: expired delivery reconciliation chooses checkpoint history over a
  duplicate injection.
- Unit: cancel-before-drain never calls `agent.steer`; cancel-after-dispatch
  calls `agent.cancel_steer`; an injection race resolves to injected.
- Unit: Redis control tests assert that wake-ups contain IDs but never steering
  content or sender data, while ordinary live steer messages remain unchanged.
- Backend e2e: reconciliation finalizes rows after every current stale-marking
  entry point and after an `errored` resume; each reconciliation and purge pass
  respects its 100-row bound.

## Unit 3 — Workspace API, HITL routing, and bootstrap

### Files

- Modify `backend/cubeplex/api/routes/v1/conversations.py` to enqueue steering,
  cancel queue rows, expose pending rows in bootstrap, and resolve long-pause
  run IDs from the CubePi checkpointer.
- Update `backend/tests/unit/test_conversations_bootstrap_pending_hitl.py`.
- Update `backend/tests/e2e/test_hitl_pause_resume.py` to replace the paused
  steering 409 contract with durable queue behavior.
- Update `backend/tests/e2e/test_steer_endpoint.py` for the conditional
  HITL-durable versus ordinary-live response contract.
- Create `backend/tests/e2e/test_hitl_queued_steering.py` for cross-store,
  cross-worker, and ordering invariants that do not belong in route unit tests.

### Interfaces

`POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/steer` keeps
the request body:

```json
{"content":"Use the smaller dataset","steer_id":"steer-..."}
```

It returns one of:

```json
{"status":"queued","run_id":"...","steer_id":"steer-..."}
{"status":"dispatched","run_id":"...","steer_id":"steer-..."}
{"status":"injected","run_id":"...","steer_id":"steer-..."}
{"status":"cancelled","run_id":"...","steer_id":"steer-..."}
{"status":"failed","run_id":"...","steer_id":"steer-..."}
{"status":"steered","run_id":"...","steer_id":"steer-..."}
{"status":"published","run_id":"...","steer_id":"steer-..."}
{"status":"no_active_run","run_id":null,"steer_id":"steer-..."}
```

The five queue states are the actual current state of a durable row, including
an existing row returned by an idempotent retry. `steered` and `published` are
the unchanged ordinary live-run outcomes. The route trims content and rejects
an empty text instruction, matching the normal text-message rule. It also
rejects more than 32 KiB with HTTP 413/`steer_content_too_large`, and rejects a
run or conversation queue cap with HTTP 429/`steer_queue_full`. Both errors
leave the draft intact. The route retains the current participant auto-join and
sender snapshot behavior. Reusing an existing durable `steer_id` with different
normalized content or a different sender returns HTTP
409/`steer_id_conflict` without changing the row.

`POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/steer/cancel`
first locates a durable row by scoped conversation and client steer ID. If none
exists, it keeps the current Redis live-run cancellation path. Durable responses
are:

```text
cancelled         queued or failed becomes a 24-hour tombstone
accepted          dispatched item entered cancel_requested
already_injected  persisted history already owns the message
not_found         no visible row matches
```

Each response includes the row's `run_id` when a row was found.

`GET /api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap`
adds:

```json
"pending_steers": [
  {
    "steer_id":"steer-...",
    "content":"Use the smaller dataset",
    "state":"queued",
    "created_at":"2026-08-12T12:00:00Z"
  }
]
```

Only `queued`, `dispatched`, and `failed` are returned. `cancel_requested`,
`cancelled`, and `injected` do not render as pending chips.

### Core logic

- Before current-run routing, look up `(conversation_id, steer_id)` in the
  scoped durable repository. Return an existing row's actual state and original
  run even if the conversation now has a newer run, but reject a content or
  sender mismatch as `steer_id_conflict`.
- Next resolve a checkpointed `pending_request` and matching `pending_run_id`.
  If they exist, use the durable queue even if Redis expired or already says
  `running`. If no HITL request exists, preserve the current Redis-only live
  `dispatch_steer` path. A terminal Redis status is not an enqueue target.
- Commit the idempotent queue row before publishing `steer_available`.
- On a uniqueness conflict, reload by scoped conversation and client ID and
  return the existing row's actual state; never report an injected, cancelled,
  or failed row as newly queued.
- Recheck ownership after commit. If the matching HITL request was cleared but
  the same run is still active, keep the row and notify the live owner. If the
  run is neither active nor the owner of that pending request, mark the row
  failed and return its `failed` state. Bootstrap and the coordinator's bounded
  reconciliation scan repair rows whose terminal cleanup raced enqueue.
- Keep the paused HITL question authoritative until CubePi checkpoints its tool
  result. Steering submitted after form POST but before that clear therefore
  still resolves to the same run.
- Leave `claim_resume` unchanged: `errored`, completed, cancelled, or mismatched
  runs remain conflicts. An errored run finalizes uninjected steering as failed
  instead of risking a second execution of partial resume side effects.
- Use a separate scoped session for queue hydration if it runs concurrently
  with bootstrap history reads; do not issue concurrent operations on the
  route's shared `AsyncSession`.
- Preserve workspace `require_member` behavior and scoped 404s. Do not add an
  admin route or a `scope` parameter.

### Tests

- Unit: bootstrap serializes queue states and timezone-aware timestamps, and
  omits terminal/cancel-requested rows.
- Backend e2e: paused `ask_user` and sandbox confirmation steering return 202
  queued instead of 409.
- Backend e2e: duplicate POSTs with one `steer_id` produce one queue row and one
  checkpointed user message, and retries return each existing terminal or
  in-flight state truthfully.
- Backend e2e: reusing one durable `steer_id` with different content or sender
  returns `steer_id_conflict` and leaves the original row unchanged.
- Backend e2e: a delayed retry during a newer run returns the old run's
  24-hour tombstone instead of entering the new run; expiry cleanup is bounded.
- Backend e2e: a running run without a persisted HITL request still uses the
  existing direct `steered`/`published` path and creates no queue row.
- Backend e2e: Redis expiry during a long HITL pause still resolves through
  `pending_run_id`, and bootstrap restores the queued row.
- Backend e2e: cross-workspace enqueue, cancel, and bootstrap reads return the
  existing scoped 404 behavior.
- Backend e2e: an enqueue/run-terminal race yields either one injected history
  message or one visible failed row, never a silent disappearance.

## Unit 4 — Frontend lifecycle, composer, and pending chips

### Files

- Modify `frontend/packages/core/src/api/stream.ts` for the durable enqueue and
  cancel response unions.
- Modify `frontend/packages/core/src/api/runStreams.ts` for bootstrap
  `pending_steers` and its row type.
- Modify `frontend/packages/core/src/stores/messageStore.ts` for
  per-conversation run lifecycle, durable pending-steer hydration, enqueue
  results, cancellation results, and one-shot stale-send recovery.
- Modify `frontend/packages/web/components/layout/InputBar.tsx` so composer
  routing and attachment availability use run lifecycle.
- Modify `frontend/packages/web/components/layout/PendingSteers.tsx` to render
  queued/dispatched/failed state and provide cancel, move-to-draft, and dismiss
  actions.
- Modify `frontend/packages/web/components/chat/MessageList.tsx` so HITL answer,
  approve/deny, and cancel-as-answer transitions set `resuming_hitl` without
  locking the composer.
- Reuse `frontend/packages/web/hooks/useComposerDraft.ts` when moving failed
  text back to the composer; do not add a second draft bridge.
- Modify `frontend/packages/web/messages/en.json` and
  `frontend/packages/web/messages/zh.json` for queued, waiting-for-HITL,
  failed, retry, and dismiss copy.
- Update the existing core API/store and web component tests named below.

### Interfaces

Add these core types:

```ts
type RunLifecycle = 'idle' | 'running' | 'paused_hitl' | 'resuming_hitl' | 'stopping'

type SteerRunStatus =
  | 'queued'
  | 'dispatched'
  | 'injected'
  | 'cancelled'
  | 'failed'
  | 'steered'
  | 'published'
  | 'no_active_run'

type PendingSteerState =
  | 'submitting' // local-only optimistic state
  | 'queued'
  | 'dispatched'
  | 'failed'

type PendingSteer = {
  steerId: string
  text: string
  state: PendingSteerState
  createdAt: string
}
```

`messageStore.runLifecycleByConversation` is the composer-routing authority.
`isStreaming` remains the visual signal for assistant output and loading UI.

`messageStore.steer(...)` returns the server status instead of collapsing it to
a boolean. `queued` and `dispatched` retain a pending chip; `injected` does not;
`failed` becomes a failed chip; `cancelled`, `no_active_run`, queue validation
errors, `steer_id_conflict`, and request errors restore the text for user
control. `steered` and `published` retain the ordinary live behavior.
`cancelSteer(...)` returns its server outcome so the failed-row UI moves text
into `useComposerDraft` only after dismissal wins.

### Core logic

- Bootstrap derives lifecycle as follows:
  - no active run and no pending HITL → `idle`;
  - pending HITL plus an active run already back in `running` →
    `resuming_hitl`;
  - pending HITL otherwise → `paused_hitl`;
  - active running run → `running`.
- Live HITL request events set `paused_hitl`. Submitting any HITL decision sets
  `resuming_hitl`. If the answer POST rejects or fails, refresh bootstrap; a
  still-pending request restores `paused_hitl`. A paused `done` returns to
  `paused_hitl`; a true terminal event returns to `idle`; hard Stop sets
  `stopping` until bootstrap/terminal confirmation makes it idle.
- Composer Enter starts a new turn only in `idle`. It calls `steer` in
  `running`, `paused_hitl`, and `resuming_hitl`, and is disabled in `stopping`.
- The textarea is enabled during both HITL states. The form owns only its own
  submit/duplicate-prevention state. Empty paused input has no Stop button;
  the HITL card remains the way to answer or cancel the question.
- Attachments and attachment slash actions remain disabled for every lifecycle
  other than idle. An attachment-bearing 409 preserves text and staged files
  and does not retry as steering.
- A text-only `active_run_conflict` refreshes bootstrap once. If the refreshed
  lifecycle is running/paused/resuming, retry the unchanged text once through
  `/steer` with one generated `steer_id`; never loop between send and steer.
- InputBar snapshots and clears submitted text synchronously when it creates the
  local `submitting` chip, so the textarea remains ready while the request is in
  flight. If the request is not accepted, restore with
  `currentDraft ? submitted + "\n" + currentDraft : submitted`; never run an
  asynchronous unconditional clear.
- Merge bootstrap's server rows with a local `submitting` row instead of
  clearing the optimistic chip during an in-flight enqueue. Server rows replace
  matching client IDs once committed.
- `injected_message` keeps its current job: commit the user message at CubePi's
  actual history position and remove the matching pending chip. The enqueue
  response reducer first checks transcript messages for that `steer_id`; if SSE
  already committed it, no response state may recreate the chip. Conversely,
  an `injected` response whose message is not local triggers one bootstrap
  refresh because the checkpointed history is already authoritative.
- Failed chips offer two explicit actions when lifecycle is idle:
  - **Move to draft:** cancel/dismiss the failed server row, then place its text
    in the existing composer draft bridge for user-controlled resubmission.
  - **Dismiss:** cancel/dismiss the row without changing the draft.
  Neither action automatically starts a new run.

### Tests

- Update `frontend/packages/core/__tests__/api/stream.test.ts` for `queued`,
  every durable stored state, the ordinary live statuses, and the two bounded
  queue errors plus `steer_id_conflict`.
- Update `frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts`,
  `messageStorePendingSteer.test.ts`, and
  `messageStoreCommitSteer.test.ts` for server state, bootstrap merging,
  snapshot-safe draft restoration, cancellation races, injection idempotency,
  `injected_message` arriving before the enqueue response, and an `injected`
  response repairing a missing local SSE event through bootstrap.
- Update
  `frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts`
  for paused/resuming lifecycle derivation, failed-answer rollback, and queue
  restoration.
- Update
  `frontend/packages/core/__tests__/stores/messageStoreSendConflict.test.ts`
  for a single text-only send→bootstrap→steer recovery and the no-retry
  attachment case.
- Update `frontend/packages/web/__tests__/components/InputBar.test.tsx` and
  `InputBar.slash.test.tsx` for enabled HITL text, steer routing, hard-stop
  locking, and attachment restrictions.
- Update `frontend/packages/web/__tests__/components/PendingSteers.test.tsx`
  for state labels, cancellability, move-to-draft, and dismiss behavior. Tests
  assert actions and state transitions, not mere element presence.

## Unit 5 — Full business flow, documentation, and verification

### Files

- Extend `frontend/packages/web/__tests__/e2e/steering.spec.ts` with a
  deterministic browser flow for queued steering during HITL, using the
  existing outer-model test seam for fixed tool calls and responses.
- Modify `docs/site/docs/guides/conversations/basics.md` in the implementation
  PR to replace the current composer-lock description with the accepted queue
  behavior and text-only limitation.

### Interfaces

No new production interface is introduced here. This unit verifies the API,
SSE, history, and browser contracts from the earlier units as one user flow.

### Core logic

The browser scenario is:

```text
Agent emits ask_user
→ user sends one text steering message and waits for queued
→ user sends a second text steering message and waits for queued
→ pending chips show durable queued state
→ page reload restores the card and chips
→ user submits the HITL form
→ chips become transcript user messages in accepted order
→ Agent responds with the HITL answer and steering in context
→ another reload preserves the same role/message order
```

The backend companion test asserts checkpoint history directly:

```text
assistant tool_use
→ matching tool_result
→ steer 1 UserMessage
→ steer 2 UserMessage
→ next AssistantMessage
```

Run the sandbox approve and deny ordering variants at backend e2e level; the
browser test needs only one complete `ask_user` flow. Keep Postgres, Redis,
FastAPI, SSE, and the browser real, but make the outer model deterministic so
the test does not depend on whether a model chooses to call `ask_user` or echo
both inputs.

### Tests and verification

During implementation, follow red→green→refactor per unit and run only the
changed slices, capturing noisy output under `tmp/`:

```bash
cd backend
uv run pytest \
  tests/unit/test_public_id.py \
  tests/unit/test_steering_delivery.py \
  tests/unit/test_conversations_bootstrap_pending_hitl.py \
  --no-cov 2>&1 | tee tmp/hitl-queued-unit.log | tail -3

uv run pytest \
  tests/e2e/test_steering_message_repository.py \
  tests/e2e/test_hitl_queued_steering.py \
  tests/e2e/test_hitl_pause_resume.py \
  tests/e2e/test_steer_endpoint.py \
  --no-cov 2>&1 | tee tmp/hitl-queued-e2e.log | tail -3

uv run alembic upgrade head
uv run alembic check
```

```bash
cd frontend
pnpm --filter @cubeplex/core test -- \
  messageStoreSteer.test.ts \
  messageStorePendingSteer.test.ts \
  messageStoreCommitSteer.test.ts \
  messageStore.bootstrapPendingHitl.test.ts \
  messageStoreSendConflict.test.ts \
  2>&1 | tee ../tmp/hitl-queued-core.log | tail -3

pnpm --filter web test -- \
  InputBar.test.tsx InputBar.slash.test.tsx PendingSteers.test.tsx \
  2>&1 | tee ../tmp/hitl-queued-web.log | tail -3

pnpm exec playwright test packages/web/__tests__/e2e/steering.spec.ts \
  2>&1 | tee ../tmp/hitl-queued-playwright.log | tail -3
```

Build `@cubeplex/core` before the final web type/build verification. The final
code push uses the normal pre-push hook, which runs `make check-ci`; do not run
that full command separately first.

## Coverage review

| Approved spec requirement | Owning unit |
|---|---|
| Durable, idempotent, deterministically ordered HITL steering storage | Unit 1 |
| Atomic byte/count limits and bounded bootstrap payload | Units 1 and 3 |
| Delayed retry returns actual state instead of targeting a newer run | Units 1, 3, and 4 |
| Cross-worker delivery without Redis as source of truth | Unit 2 |
| Registration, notification, and poll drains are single-flight per run | Unit 2 |
| Tool result before queued user messages | Units 2 and 5 |
| Cancel, lease recovery, acknowledgement reconciliation | Units 1 and 2 |
| Paused/resuming API accepts steering without 409 | Unit 3 |
| Long-pause Redis expiry and safe errored-run finalization | Units 2 and 3 |
| Workspace isolation and sender identity | Units 1 and 3 |
| Composer unlocked for text but not attachments | Unit 4 |
| In-flight typing and SSE-before-response cannot lose text or recreate chips | Unit 4 |
| Explicit idle/running/paused/resuming/stopping routing | Unit 4 |
| Refresh restores pending chips; injection commits transcript | Units 3–5 |
| Stale text send retries once as steering | Unit 4 |
| Failed text is visible and recoverable, never silently dropped | Units 1, 3, and 4 |
| Ordinary live steering retains its current direct path | Units 2–4 |
| User-facing documentation ships with behavior | Unit 5 |

## Completion criteria

- Every success criterion in the approved spec is backed by a named test above.
- The migration is autogenerated, has one head, upgrades the worktree database,
  and leaves `alembic check` clean.
- Backend source remains mypy-strict and frontend types remain strict; source
  lines stay within the repository's 100-character limit.
- The full browser flow demonstrates no paused/submitting HITL 409, no lost
  accepted text, and stable history order after reload.
- The implementation PR contains the conversation-guide update and no unrelated
  refactor.
