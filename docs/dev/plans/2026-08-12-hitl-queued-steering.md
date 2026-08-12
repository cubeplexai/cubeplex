# HITL queued steering implementation plan

- **Goal:** Keep the conversation composer usable during HITL and deliver every
  accepted text instruction to the existing run without 409s, message loss, or
  invalid prompt history.
- **Architecture:** A workspace-scoped Postgres queue becomes the source of
  truth for steering. The API commits a row before acknowledging it; Redis only
  wakes the worker that owns the run; a delivery coordinator claims rows and
  passes them through `agent.steer(...)`. The frontend routes composer text by
  an explicit per-conversation run lifecycle rather than by streaming visuals.
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
- optional `hitl_question_id` captured when enqueue happens;
- `state`, optional `delivery_owner`, and timezone-aware optional
  `delivery_lease_until`;
- inherited timezone-aware `created_at` and `updated_at`.

The table has a unique constraint on `(run_id, client_steer_id)`, a foreign key
to the conversation, the normal org/workspace scope index, and an ordered
delivery index on `(run_id, state, created_at, id)`.

The scoped repository exposes typed operations for these contracts:

- idempotently enqueue by `(run_id, client_steer_id)`, returning the existing
  row on a request retry;
- list visible `queued`, `dispatched`, and `failed` rows for one conversation;
- claim a FIFO batch for one run with `FOR UPDATE SKIP LOCKED`, including an
  expired lease owned by a dead worker;
- return a synchronously failed delivery claim to `queued`;
- transition `queued` directly to `cancelled`, delete a dismissed `failed` row,
  and transition `dispatched` to `cancel_requested`;
- mark an owned row `cancelled`, `injected`, or `failed` with compare-and-set
  predicates so a cancellation/injection race has one winner;
- delete injected/cancelled tombstones and mark remaining active rows failed
  when a logical run really terminates.

Repository methods participate in the caller's transaction. A route, delivery
claim, or cleanup operation commits all of its state transition at once rather
than relying on multiple per-row commits.

### Core logic

- Store status as a bounded string column and use `SteeringMessageState` at
  Python boundaries, matching existing queue models without introducing a
  PostgreSQL enum lifecycle.
- Treat `(created_at, id)` as the FIFO tie-breaker. The sortable public ID makes
  equal timestamps deterministic.
- Keep injected/cancelled tombstones until logical run cleanup so a retried POST
  cannot recreate and inject the same client ID.
- Preserve `failed` rows and their content for bootstrap. They are deleted only
  after the user dismisses or moves the text back to the composer.
- User-facing conversation deletion is soft and therefore does not trigger an
  FK cascade. Explicitly delete these transient rows before stamping
  `Conversation.deleted_at`; do not change retention for history, artifacts,
  billing, or attachments.

### Tests

- Unit: `stm` IDs have the expected shape and fit the existing 20-character
  column limit.
- Backend e2e: duplicate enqueue returns one row; FIFO claims are stable; two
  claimers cannot own the same row; expired leases are reclaimable; each valid
  and invalid state transition is enforced.
- Backend e2e: org/workspace-scoped reads and mutations cannot see another
  workspace's rows.
- Backend e2e: soft conversation deletion removes uninjected steering rows,
  while physical deletion also satisfies the FK contract.

## Unit 2 — Delivery coordinator and RunManager integration

### Files

- Create `backend/cubeplex/streams/steering_delivery.py` for delivery claims,
  CubePi message construction, cancellation, history reconciliation, and the
  bounded fallback poll.
- Modify `backend/cubeplex/streams/run_manager.py` to use durable queue
  notifications, attach/detach Agents to the coordinator, acknowledge injected
  messages, and finalize queue rows with the logical run.
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
finalize_run(run_id, ctx, has_pending_hitl) -> None
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

The former `steer` control message containing user text is removed in the clean
cutover. Hard-run cancellation and its acknowledgement channel stay unchanged.

### Core logic

1. After either prompt-path or respond-path code registers
   `self._agents[run_id]`, it synchronously drains durable rows before calling
   `agent.prompt(...)` or `agent.respond(...)`.
2. Claiming commits `state=dispatched`, `delivery_owner`, and a short lease
   before calling the synchronous `agent.steer(...)`. If that call raises, the
   same owner returns the row to `queued`.
3. The control listener wakes the owner for low latency. One process-level
   two-second fallback poll drains only run IDs currently present in that
   process's Agent registry; Redis loss cannot strand a committed row.
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
7. Run cleanup deletes tombstones and marks uninjected rows failed only when
   the logical run is terminal. `paused_hitl`, or `errored` with a persisted
   HITL request that can be retried, leaves rows recoverable. Hard Stop,
   completed, cancelled, stale, and unrecoverable error paths finalize them.
8. `_force_cancel_hitl` finalizes rows belonging to the old run before a new
   run is allowed to claim the conversation.

`RunManager.start_control_listeners()` starts the coordinator poll after its
Redis listeners, and `stop_control_listeners()` stops the poll before closing
those listeners. No second application-lifespan owner is introduced.

### Tests

- Unit: queued rows are converted to CubePi `UserMessage` objects with
  `steer_id`, sender metadata, text, and FIFO order intact.
- Unit: startup drain happens after Agent registration but before both
  `prompt` and `respond` calls.
- Unit: missed Pub/Sub is recovered by the bounded poll without creating a
  per-run polling task.
- Unit: a synchronous dispatch failure returns the row to queued; a failed
  acknowledgement still publishes `injected_message`.
- Unit: expired delivery reconciliation chooses checkpoint history over a
  duplicate injection.
- Unit: cancel-before-drain never calls `agent.steer`; cancel-after-dispatch
  calls `agent.cancel_steer`; an injection race resolves to injected.
- Unit: Redis control tests assert that wake-ups contain IDs but never steering
  content or sender data.

## Unit 3 — Workspace API, HITL recovery, and bootstrap

### Files

- Modify `backend/cubeplex/api/routes/v1/conversations.py` to enqueue steering,
  cancel queue rows, expose pending rows in bootstrap, and resolve long-pause
  run IDs from the CubePi checkpointer.
- Modify `backend/cubeplex/streams/hitl_resume.py` so a matching persisted HITL
  request can retry a resume whose prior worker ended in `errored` before
  clearing the request.
- Update `backend/tests/unit/test_hitl_claim_resume.py` and
  `backend/tests/unit/test_conversations_bootstrap_pending_hitl.py`.
- Update `backend/tests/e2e/test_hitl_pause_resume.py` to replace the paused
  steering 409 contract with durable queue behavior.
- Update `backend/tests/e2e/test_steer_endpoint.py` for the durable live-run
  response and persistence contract.
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
{"status":"no_active_run","run_id":null,"steer_id":"steer-..."}
```

The route trims content and rejects an empty text instruction, matching the
normal text-message rule. It retains the current participant auto-join and
sender snapshot behavior.

`POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/steer/cancel`
locates the durable row by the scoped conversation and client steer ID; it no
longer requires a current Redis active-run pointer. Responses are:

```text
cancelled         queued becomes a tombstone; failed is dismissed/deleted
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

- Resolve enqueue ownership from a Redis active run in `running` or
  `paused_hitl`; if Redis expired, use the checkpointer's matching
  `pending_request` and `pending_run_id`. A terminal Redis status is not an
  enqueue target.
- Commit the idempotent queue row before publishing `steer_available`.
- Recheck ownership after commit. If the same run is neither active nor the
  owner of a persisted HITL request, mark the row failed and return
  `no_active_run`. Bootstrap also reconciles old rows whose terminal cleanup
  raced an enqueue, ensuring they surface as failed instead of remaining
  queued forever.
- Keep the paused HITL question authoritative until CubePi checkpoints its tool
  result. Steering submitted after form POST but before that clear therefore
  still resolves to the same run.
- Let `claim_resume` reclaim `errored` only through the existing call path that
  has already verified a matching DB pending request and run ID. Completed,
  cancelled, or mismatched runs remain conflicts.
- Use a separate scoped session for queue hydration if it runs concurrently
  with bootstrap history reads; do not issue concurrent operations on the
  route's shared `AsyncSession`.
- Preserve workspace `require_member` behavior and scoped 404s. Do not add an
  admin route or a `scope` parameter.

### Tests

- Unit: bootstrap serializes queue states and timezone-aware timestamps, and
  omits terminal/cancel-requested rows.
- Unit: resume claim accepts `errored` and continues to reject completed or
  cancelled state; route/e2e coverage proves the claim is never called without
  the matching persisted pending request precondition.
- Backend e2e: paused `ask_user` and sandbox confirmation steering return 200
  queued instead of 409.
- Backend e2e: duplicate POSTs with one `steer_id` produce one queue row and one
  checkpointed user message.
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

`messageStore.steer(...)` returns whether the server accepted the row. The
InputBar clears the textarea only on `queued`; `no_active_run` or a request
error leaves the draft intact. `cancelSteer(...)` returns its server outcome so
the failed-row UI moves text into `useComposerDraft` only after dismissal wins.

### Core logic

- Bootstrap derives lifecycle as follows:
  - no active run and no pending HITL → `idle`;
  - pending HITL plus an active run already back in `running` →
    `resuming_hitl`;
  - pending HITL otherwise → `paused_hitl`;
  - active running run → `running`.
- Live HITL request events set `paused_hitl`. Submitting any HITL decision sets
  `resuming_hitl`. A paused `done` returns to `paused_hitl`; a true terminal
  event returns to `idle`; hard Stop sets `stopping` until bootstrap/terminal
  confirmation makes it idle.
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
- Merge bootstrap's server rows with a local `submitting` row instead of
  clearing the optimistic chip during an in-flight enqueue. Server rows replace
  matching client IDs once committed.
- `injected_message` keeps its current job: commit the user message at CubePi's
  actual history position and remove the matching pending chip.
- Failed chips offer two explicit actions when lifecycle is idle:
  - **Move to draft:** cancel/dismiss the failed server row, then place its text
    in the existing composer draft bridge for user-controlled resubmission.
  - **Dismiss:** cancel/dismiss the row without changing the draft.
  Neither action automatically starts a new run.

### Tests

- Update `frontend/packages/core/__tests__/api/stream.test.ts` for `queued`,
  `accepted`, and `already_injected` response contracts.
- Update `frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts`,
  `messageStorePendingSteer.test.ts`, and
  `messageStoreCommitSteer.test.ts` for server state, bootstrap merging,
  accepted-only draft clearing, cancellation races, and injection idempotency.
- Update
  `frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts`
  for paused/resuming lifecycle derivation and queue restoration.
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

- Extend `frontend/packages/web/__tests__/e2e/steering.spec.ts` with the real
  browser flow for queued steering during HITL.
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
→ user sends two text steering messages while the card is pending
→ pending chips show durable queued state
→ page reload restores the card and chips
→ user submits the HITL form
→ chips become transcript user messages in FIFO order
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
browser test needs only one complete `ask_user` flow. The Playwright test uses
the repository's existing real-LLM gate rather than adding an unmarked model
call.

### Tests and verification

During implementation, follow red→green→refactor per unit and run only the
changed slices, capturing noisy output under `tmp/`:

```bash
cd backend
uv run pytest \
  tests/unit/test_public_id.py \
  tests/unit/test_steering_delivery.py \
  tests/unit/test_hitl_claim_resume.py \
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
| Durable, idempotent, FIFO steering storage | Unit 1 |
| Cross-worker delivery without Redis as source of truth | Unit 2 |
| Tool result before queued user messages | Units 2 and 5 |
| Cancel, lease recovery, acknowledgement reconciliation | Units 1 and 2 |
| Paused/resuming API accepts steering without 409 | Unit 3 |
| Long-pause Redis expiry and failed-resume retry | Unit 3 |
| Workspace isolation and sender identity | Units 1 and 3 |
| Composer unlocked for text but not attachments | Unit 4 |
| Explicit idle/running/paused/resuming/stopping routing | Unit 4 |
| Refresh restores pending chips; injection commits transcript | Units 3–5 |
| Stale text send retries once as steering | Unit 4 |
| Failed text is visible and recoverable, never silently dropped | Units 1, 3, and 4 |
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
