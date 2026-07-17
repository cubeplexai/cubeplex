# Workspace Scheduled Tasks — Design (#150)

Status: draft
Date: 2026-05-27
Issue: #150 (related: triggers #152, managed agents #153, IM #149, sandbox
ownership #144)

---

## Problem & motivation

Today an agent run only starts when a logged-in user is sitting in front of a
conversation and sends a message. There is no way to say "run this prompt every
weekday at 9am" or "in two hours, summarize what changed." Users who want a
recurring report, a periodic check, or a one-off deferred run have to babysit
the chat.

Workspace **scheduled tasks** let a workspace member define a task that fires
agent runs on a schedule (recurring cron, fixed interval, or a single future
time). Each fire produces a normal run in a target conversation, attributed to
the owning user, with the same tools/memory/cost accounting as an interactive
run. Firing must survive replica restarts, must not double-fire across
replicas, must have an explicit policy for runs missed while the system was
down, and must leave an observable history of what fired and what happened.

A schedule is one kind of *trigger* (#152). The same "something external
decided it's time to run an agent" path will later back managed agents (#153)
and inbound IM messages (#149). This spec builds the scheduled-task slice but
names the shared seam so #152/#153 don't have to retrofit it.

---

## Goals / Non-goals

### Goals

- CRUD + pause/resume for scheduled tasks, scoped to a workspace.
- Three schedule kinds: cron expression, fixed interval, single future
  datetime (one-shot).
- Each task carries: the run spec (prompt + optional agent/params), a target
  conversation policy (reuse a fixed conversation or create a fresh one per
  fire), and the owning user identity the run executes as.
- Reliable firing across multiple backend replicas — at-least-once delivery
  with single-fire-per-occurrence in the common case, and idempotency so a
  rare double-claim cannot produce two runs for the same occurrence.
- Explicit missed-run policy (what happens to occurrences that fell due while
  no replica was running, or while the task was paused).
- Run history: every fire is recorded with its scheduled time, actual time,
  resulting run id / conversation id, and outcome (started / skipped / failed).
- Scope-isolated workspace API and page.

### Non-goals (v1)

- No org-admin / cross-workspace scheduled-task management surface. (A separate
  admin route family if ever needed — never a `?scope=` param.)
- No event-driven (non-time) triggers — those are #152's job; we only define
  the shared seam.
- No managed-agent autonomy loop (#153) — a scheduled task fires a single run,
  not an open-ended agent session.
- No per-task sandbox lifecycle ownership decisions beyond reusing the existing
  per-(user, workspace) sandbox model (see Identity section, #144).
- No catch-up replay of *many* missed occurrences (e.g. "run the 200 fires we
  missed over the weekend"). v1 missed-run policy is run-latest-once-or-skip,
  not backfill-all.
- No sub-minute schedules. Minimum granularity is one minute.

---

## Current state

### How a run starts today

The only run-start path is `RunManager.start_run(...)`
(`backend/cubeplex/streams/run_manager.py`). It is called from the HTTP handler
`send_message` in `backend/cubeplex/api/routes/v1/conversations.py`. The flow:

1. Validate the conversation exists and attachments are valid.
2. Mark the conversation active (`_update_conversation_timestamp`).
3. Build a `RunContext(user_id, org_id, workspace_id)`.
4. `run_manager.start_run(conversation_id=..., content=..., attachments=...,
   ctx=...)` — this claims a per-conversation active-run lock in Redis
   (`create_run`), spawns an in-process `asyncio.Task` running the cubepi agent,
   and returns a `run_id`.
5. The HTTP response streams that run's events over SSE from a Redis stream.

Key facts that shape this design:

- A run is an **in-process asyncio task** on whichever replica called
  `start_run`. There is no external worker pool; the run lives and dies with
  that replica's process. `RunManager` keeps `self._tasks[run_id]`.
- The active-run lock and the event log both live in Redis, keyed by
  conversation. `start_run` raises if the conversation already has a `running`
  run (one active run per conversation).
- Cross-replica control (cancel / steer) is done over Redis pub/sub
  (`start_control_listeners`, the `:control` / `:control:ack` channels). See
  `docs/dev/specs/2026-05-25-multi-instance-run-control-design.md`. So the
  pattern of "any replica can drive a run that physically lives on another
  replica" already exists for control, but **starting** a run is always local
  to the replica that received the HTTP request.
- `RunContext` carries the identity the run executes as. For a scheduled task
  there is no live request, so we must persist and reconstruct this identity.

The cubepi agent build inside `_run_cubepi_path` resolves the workspace's
provider/model, composes tools (sandbox, MCP, memory, skills…), and writes
billing via `CostMiddleware`. A scheduled run gets all of this for free **if**
it goes through `start_run` with a valid `RunContext` — that is the design
constraint: do not fork the run-start path, feed it.

### Worker / queue / recurring-job infra

There is **none today**. Grepping `backend/cubeplex` for
scheduler/cron/celery/apscheduler/recurring/background_task returns nothing. The
only background execution is the per-run asyncio task inside `RunManager`, plus
memory-consolidation tasks it tracks. Redis is used heavily (run event streams,
active-run locks, pub/sub control, OAuth token cache) but never as a job queue.

> Note: `backend/.claude/scheduled_tasks.lock` is unrelated — it is a stray
> Claude Code session lock file (`{"sessionId":...,"pid":...}`), not part of any
> cubeplex feature. It can be ignored / gitignored.

### Deployment topology

Multi-replica. `backend/docs/deploy-k8s-graceful-restart.md` documents rolling
restarts with `terminationGracePeriodSeconds`, split liveness/readiness probes,
and in-flight run draining on SIGTERM. So we must assume **N replicas of the
same FastAPI process**, each with its own `RunManager`, all sharing one Postgres
and one Redis. There is no existing leader-election primitive. Any scheduler we
add must therefore either elect a leader or be safe to run concurrently on every
replica.

### Public-ID + model conventions

New business tables subclass `CubeplexBase` (+ `OrgScopedMixin` for
org/workspace scoping) and set a `_PREFIX` ClassVar; the PK auto-fills via
`generate_public_id(_PREFIX)` in `model_post_init`
(`backend/cubeplex/models/mixins.py`, `public_id.py`). Shared prefixes used by
non-mixin tables live as constants in `public_id.py`; per-table prefixes live on
the model. Conversations use `_PREFIX = "conv"`; sandboxes `"sbx"`.

---

## Research — scheduling approaches

The core question: in an N-replica FastAPI + Postgres + Redis deployment with
**no existing worker fleet**, how do we fire time-based jobs reliably,
exactly-ish once, with a clear missed-run policy?

### Option A — Celery beat + Celery workers

Celery is a distributed task queue; `celery beat` is its periodic scheduler.
Built for multi-machine fan-out. But: it needs a broker and a separate worker
deployment, and `beat` itself is classically a **single point of truth that
must not run twice** — running two beats double-fires unless you add a lock.
This adds a whole new runtime (broker + worker pods + beat pod) for a feature
whose actual work (the agent run) already executes inside the FastAPI process
via `RunManager`. The run cannot trivially move to a Celery worker because it
depends on `app.state` (encryption backend, tracer, MCP token signer, Redis
streaming runtime). Heavy; poor fit. (StackShare, Leapcell comparisons.)

### Option B — APScheduler

APScheduler 3.x is an in-process scheduler with pluggable jobstores
(SQLAlchemy/Postgres for persistence). Simple, but 3.x is **not safe to run on
multiple replicas against one jobstore** without external coordination — every
replica's scheduler would fire. APScheduler 4.0 redesigned datastores to
support *multiple running schedulers sharing one Postgres datastore* with an
event broker, and adds `misfire_grace_time` (jobs past `scheduled_time +
grace` are released as `missed_start_deadline` instead of fired) — which is
exactly a built-in missed-run policy. 4.0 is, however, still pre-release / in
progress (issue #465), and adopting it couples our reliability story to a beta
dependency. (APScheduler 4.0 user guide / migration docs.)

### Option C — DB-backed schedule rows + a poller, claimed via SELECT … FOR UPDATE SKIP LOCKED

Store each task as a Postgres row with a `next_fire_at` column. Every replica
runs a lightweight poller (e.g. wake every ~15–30s). On wake, in one
transaction: `SELECT … WHERE next_fire_at <= now() AND status='active' FOR
UPDATE SKIP LOCKED LIMIT k`, fire each claimed row, then compute and write its
next `next_fire_at` in the same transaction. `SKIP LOCKED` is the standard
Postgres job-queue primitive (PG 9.5+): two pollers cannot grab the same row,
and a crash mid-transaction rolls back so the row becomes claimable again. No
new runtime — Postgres is already there. Missed-run policy is explicit and
ours: it lives in how we recompute `next_fire_at` and whether we fire when
`now() - scheduled_time > grace`. (Vlad Mihalcea / dbpro / Netdata SKIP LOCKED
writeups.)

### Option D — leader-elected single scheduler

Elect one replica (Redis lock with TTL, or Postgres advisory lock) to be "the
scheduler"; only it polls and dispatches. Simpler mental model (one firing
brain) but introduces leader-election failure modes (lock expiry during a GC
pause → brief double-leader, or no leader → silent stall) and a single chokepoint.

### Recommendation

**Option C: DB-backed schedule rows + a per-replica poller claiming due rows
with `SELECT … FOR UPDATE SKIP LOCKED`, recomputing `next_fire_at` inside the
claim transaction.** It adds zero new infrastructure, reuses Postgres
transactional guarantees we already depend on, makes the missed-run policy
explicit and testable in our own code, and degrades gracefully (any subset of
replicas keeps firing). Leader election (D) is unnecessary because `SKIP
LOCKED` already makes concurrent pollers safe; APScheduler 4.0 (B) is the same
idea wrapped in a beta dependency we don't need. We layer an
**occurrence-idempotency key** on top so even a pathological double-claim
cannot produce two runs for the same occurrence.

---

## Proposed design

### Data model

New table `scheduled_tasks` (`CubeplexBase` + `OrgScopedMixin`, `_PREFIX =
"stask"` — declared on the model the same way conversations declare `"conv"`;
prefix is 2–5 lowercase chars per `public_id.py`):

- `id` — public id, `stask`-prefixed PK.
- `org_id`, `workspace_id` — from `OrgScopedMixin`.
- `owner_user_id` (FK users) — the identity runs execute as (the
  `RunContext.user_id`).
- `name` — human label.
- `status` — `active` | `paused`. (Soft `deleted_at` like conversations.)
- Schedule spec:
  - `schedule_kind` — `cron` | `interval` | `once`.
  - `cron_expr` (nullable) — 5-field cron, minute granularity.
  - `interval_seconds` (nullable, ≥ 60).
  - `run_at` (nullable, UTC) — for `once`.
  - `timezone` — IANA tz name; cron is evaluated in this tz, stored times are
    UTC. (DB datetimes surfaced via `utc_isoformat()`.)
- Run spec:
  - `prompt` — the message content fed to the run.
  - `agent_config_id` (nullable FK) — optional named agent/params (see
    `agent_config.py`); null = workspace default.
  - `attachments` — none in v1 (scheduled prompts are text-only).
- Target conversation policy:
  - `target_mode` — `fixed` | `new_each_run`.
  - `target_conversation_id` (nullable FK) — required when `fixed`. **Must be a
    conversation owned by `owner_user_id`** (the run-identity user). Conversations
    are per-user — `ConversationRepository._scoped_select` filters by
    `creator_user_id`, and `start_run` appends to whatever `conversation_id` it is
    handed. A plain FK would let a member point a task at another member's
    conversation; the scheduled run (executing as the owner) would then write into
    a different user's thread. So ownership is enforced two ways:
    - **At create/edit**: the create/PATCH handler resolves the target through the
      owner-scoped `ConversationRepository` (filtered by `creator_user_id =
      owner_user_id`) and rejects with 404/422 if the conversation is not the
      owner's. The FK alone is not trusted for this check.
    - **At dispatch**: `resolve_target` re-validates ownership at fire time (the
      conversation could have been deleted or reassigned since create). If the
      fixed target is no longer owned by `owner_user_id`, the occurrence is
      recorded `failed` with a reason instead of writing into a foreign thread.
- Scheduler bookkeeping:
  - `next_fire_at` (UTC, indexed) — the next occurrence the poller will claim.
    NULL once a `once` task has fired or a task is paused.
  - `last_fired_at` (UTC, nullable).
- Index on `(status, next_fire_at)` for the poller's hot query; partial index
  on `deleted_at IS NOT NULL` for GC (mirrors conversations).

New table `scheduled_task_runs` (history; `CubeplexBase` + `OrgScopedMixin`,
`_PREFIX = "stkrn"`):

- `scheduled_task_id` (FK), `org_id`, `workspace_id`.
- `scheduled_for` (UTC) — the occurrence time this row represents.
- `claimed_at` (UTC) — when a poller first claimed the occurrence (inserted the row).
- `started_at` (UTC, nullable) — when `start_run` actually succeeded.
- `state` — the occurrence lifecycle (see state machine below):
  `claimed` | `started` | `succeeded` | `failed` | `skipped_missed` |
  `skipped_busy_max_retries`.
- `claim_count` (int, default 1) — how many times this occurrence has been
  claimed; bounds re-claim retries after a dispatch crash.
- `retry_count` (int, default 0) — how many times this occurrence has been
  postponed because its `fixed` target conversation was busy. Capped at 3
  (see "One-run-per-conversation interaction" above); past the cap the row is
  set terminal `skipped_busy_max_retries`.
- `next_retry_at` (UTC, nullable) — set when a busy-postpone happens; the
  poller re-picks the row once `now() >= next_retry_at`. Distinct from
  `claim_count`/`claim_timeout` (which guards dispatch crashes); this guards
  conversation-busy.
- `run_id` (nullable) — the `RunManager` run id, set once `start_run` returns.
- `conversation_id` (nullable) — where it ran.
- `detail` (nullable text) — error / skip reason.
- **Unique constraint `(scheduled_task_id, scheduled_for)`** — the
  occurrence-idempotency key. Inserting this history row is the act that claims
  an occurrence; a duplicate insert violates the constraint, so two pollers
  racing the same occurrence produce one row, not two. (Re-claim after a crash
  is an UPDATE of this row, not a second insert — see below.)

#### Occurrence state machine — at-least-once across dispatch crashes

The dangerous case is a replica that commits the claim and then dies before
`start_run` runs. If the only states were a terminal `started`, the occurrence
would look handled while no run ever started — dropped, not at-least-once. To
keep at-least-once, an occurrence row moves through states and a stale `claimed`
row is **re-claimable**:

- `claimed` — row inserted, the task's `next_fire_at` advanced, transaction
  committed. The occurrence is reserved but no run has started yet. `claimed` is
  **not** terminal.
- `started` — `start_run` returned a `run_id`; `run_id` + `started_at` recorded.
- `succeeded` / `failed` — terminal; the run finished, or it errored / was
  rejected (e.g. owner lost membership, fixed target not owner-owned). These are
  **not** set by the dispatch loop (which stops at `started`); see "Reaching a
  terminal state" below for how the run's outcome is copied back.
- `skipped_missed` / `skipped_busy_max_retries` — terminal skip outcomes
  (missed-run policy / busy fixed conversation past the 3-retry cap; the
  busy case is the busy-postpone path described under "One-run-per-
  conversation interaction").

Re-claim rule: a poller, in its claim transaction, also picks up
`scheduled_task_runs` rows still in `claimed` whose `claimed_at` is older than a
`claim_timeout` (a few poll intervals, default ~2 min) and whose `run_id` is
null. It re-claims such a row by `UPDATE … SET claimed_at = now(),
claim_count = claim_count + 1` (guarded by `FOR UPDATE SKIP LOCKED` so two
pollers don't both grab it), then dispatches it after commit. A row that
reaches `started`/terminal is never re-claimed. `claim_count` caps retries: past
a small bound (default 3) the row is set `failed` with a "max re-claims" reason
rather than retried forever. This gives at-least-once delivery — a crash before
`start_run` is retried by the next poller — at the cost of a rare duplicate if a
replica started the run but died before writing `run_id`; that duplicate window
is the explicit trade recorded in Open Question 6.

#### Reaching a terminal state — run-completion writes the outcome back

The dispatch loop only advances the occurrence to `started`; it never sees the
run finish. The occurrence reaches `succeeded`/`failed` from the run's own
terminal event: every `RunManager` run already ends by writing a terminal status
(`completed` / `failed` / `cancelled`) to its Redis run metadata. A run-
completion hook on that terminal event — the same place run metadata is
finalized — looks up the `scheduled_task_runs` row by `run_id` and `UPDATE`s it
to `succeeded` (on `completed`) or `failed` (on `failed`/`cancelled`, with the
reason in `detail`). Because the lookup is by `run_id`, interactive runs (no such
row) are a no-op. A run whose replica dies after `started` but before the
terminal event is left to the reaper, which bounds it (`max_claims` → `failed`)
rather than letting it sit in `started` forever. Exact hook placement (subscriber
vs inline finalizer) is implementation detail; the contract is that the run's
terminal event, not the dispatch loop, owns the `started → succeeded/failed`
transition.

### Scheduler component

A new `ScheduledTaskPoller`, started in the FastAPI lifespan
(`backend/cubeplex/api/app.py`) on **every** replica, alongside `RunManager`.

Loop (every ~15–30s, jittered to avoid replica thundering-herd):

1. `BEGIN`.
2. Claim work, in two parts of the same transaction, each `FOR UPDATE SKIP
   LOCKED` so concurrent pollers never grab the same row:
   - **Due tasks**: `SELECT * FROM scheduled_tasks WHERE status='active' AND
     deleted_at IS NULL AND next_fire_at <= now() ORDER BY next_fire_at FOR
     UPDATE SKIP LOCKED LIMIT k`. The `deleted_at IS NULL` filter is required:
     soft delete only stamps `deleted_at` and does not flip `status`, so without
     it the poller would keep firing a deleted task.
   - **Stale claims** (re-claim path): `scheduled_task_runs` rows still in
     `state='claimed'` with `run_id IS NULL` and `claimed_at < now() -
     claim_timeout` — occurrences a crashed replica reserved but never started.
   - **Busy-postponed rows**: `scheduled_task_runs` rows in the non-terminal
     re-claimable state with `next_retry_at IS NOT NULL` and
     `next_retry_at <= now()` and `retry_count < 3`. These are the busy-
     conversation postpones described above; the poller re-picks them and
     re-attempts dispatch.
3. For each due task:
   - Determine the occurrence time `scheduled_for` (= the `next_fire_at` we read).
   - Apply **missed-run policy** (below) to decide fire vs skip.
   - Insert a `scheduled_task_runs` row keyed `(task_id, scheduled_for)` in state
     `claimed` (or a terminal skip state for skipped occurrences). If the unique
     constraint trips, another poller already claimed this occurrence — skip.
   - Recompute and write the task's next `next_fire_at` (cron → next match in tz;
     interval → `scheduled_for + interval`; once → NULL + status stays active but
     with no next fire). Do this **in the same transaction** so the claim row +
     next-fire advance commit atomically.
4. For each stale `claimed` row: if `claim_count < max_claims`, `UPDATE` it to
   `claimed_at = now(), claim_count = claim_count + 1` (stays `claimed`, to be
   dispatched after commit); otherwise set it `failed` ("max re-claims").
5. `COMMIT`. The task's `next_fire_at` is advanced and every claimed/re-claimed
   occurrence row is `claimed`-and-locked-then-released, so no other replica
   re-claims it until `claim_timeout` lapses (which only happens if this replica
   crashes before starting the run).
6. **After commit**, for each `claimed` occurrence (newly claimed or re-claimed),
   call `run_manager.start_run(...)` on this replica with a reconstructed
   `RunContext` and the resolved target conversation, then `UPDATE` the row to
   `state='started'` with the returned `run_id` + `started_at`. The run then lives
   as a normal in-process task on this replica, identical to an interactive run.
   If `start_run` itself errors because the target conversation is busy and
   the task is `target_mode=fixed`, apply the busy-postpone path instead of
   terminating: if `retry_count < 3`, set `next_retry_at = now + 5m`,
   increment `retry_count`, and leave the row re-claimable (state stays a
   non-terminal `claimed`-equivalent); if `retry_count >= 3`, set the row
   terminal `skipped_busy_max_retries`. For other dispatch errors (owner-
   membership / owner-ownership check fails) set the row `failed`. The loop
   stops at `started`; the
   `succeeded`/`failed` outcome is written later by the run-completion hook
   (see "Reaching a terminal state" above), not here.

Why dispatch after commit, not inside the transaction: `start_run` does Redis +
async work and may run for a long time; holding a Postgres row lock across it
would be wrong. The claim row (committed in step 5) is the durable reservation;
because it stays in non-terminal `claimed` until `start_run` succeeds, a replica
that dies between commit and `start_run` leaves a stale `claimed` row that the
next poller re-claims (step 2/4) and dispatches — at-least-once, not silently
dropped. The reaper below is the bound on retries, not the recovery mechanism.

#### Missed-run policy (v1)

When the poller claims a row whose `next_fire_at` is in the past, v1 policy is
**run-latest-once**: fire at most one run — the most recent occurrence that is
still due — and skip everything older. Concretely, from the claimed
`next_fire_at`, walk the schedule forward to find the *latest* scheduled time
`<= now()` (the last occurrence the task would have fired had the poller never
slept); call it `latest_due`.

Catch-up fast-forwards in arithmetic only: it computes `latest_due` directly
(cron → last match `<= now()`; interval → `floor((now-anchor)/interval)`), it
does **not** walk and write one row per missed slot. At most **one** summary
history row is recorded for the whole skipped stretch — `skipped_missed` with a
`detail` carrying the skipped range/count (first..last, N occurrences) — never
one row per occurrence. This is what keeps a long pause/outage from recreating
the backlog/write-storm the v1 non-goal forbids.

- If `now() - latest_due <= misfire_grace` (default 5 min, configurable):
  fire `latest_due`, record one `skipped_missed` summary for the older stretch
  (if any), then advance `next_fire_at` to the next occurrence after `latest_due`.
- If `now() - latest_due > misfire_grace`: even the latest due occurrence is too
  stale to be useful (e.g. the next fire is already imminent), so record one
  `skipped_missed` summary covering it and the older ones, fire nothing, and
  advance `next_fire_at` to the next future occurrence.

The catch-up step must compute `latest_due` *before* deciding to fast-forward —
the bug to avoid is treating the *first* stale occurrence (`08:00` for an hourly
task the poller wakes at `10:02`) as the grace test and skipping straight to a
future slot, dropping the `10:00` occurrence that is actually within grace and
should fire. Only the single latest due occurrence fires; the skipped stretch is
observable through the one summary row, not a per-occurrence backfill.

`once` tasks past grace are recorded `skipped_missed` and never fire.

#### One-run-per-conversation interaction

`start_run` rejects a second run on a conversation that already has a `running`
run. For `target_mode=fixed`, if the conversation is busy at fire time, the
poller does **not** drop the occurrence and does **not** queue it. Instead it
postpones the occurrence by **5 minutes**: it sets `next_retry_at = now + 5m`
and increments `retry_count` on the occurrence row, leaving the row in a
re-claimable non-terminal state. On the next poll cycle past `next_retry_at`,
the row is picked up again and dispatch is re-attempted. After **3 retries**
that all hit the same busy conversation, the occurrence is marked terminal
`skipped_busy_max_retries` (with the count and the busy conversation id in
`detail`) and the poller moves on — no global queue is introduced. For
`target_mode=new_each_run`, a fresh conversation is created so there is never a
collision — this is the recommended default for recurring tasks.

The occurrence row therefore carries two extra fields used only by the busy-
retry path: `retry_count` (int, default 0) and `next_retry_at` (UTC, nullable).
These are part of the occurrence/run table described below; they do not change
the unique `(scheduled_task_id, scheduled_for)` key.

### Run dispatch — reuse the existing path

Dispatch is deliberately thin: resolve/create the target conversation, build a
`RunContext(user_id=owner_user_id, org_id, workspace_id)`, then call the **same**
`run_manager.start_run(...)` interactive runs use. The shared seam (for #152 /
#153) is a small `run_dispatch` service:

```
dispatch_scheduled_run(task, occurrence) ->
    conversation = resolve_target(task)          # fixed (owner-checked) or create new
    ctx = RunContext(task.owner_user_id, task.org_id, task.workspace_id)
    run_id = run_manager.start_run(conversation_id, task.prompt, ctx=ctx)
    return run_id
```

For `target_mode=fixed`, `resolve_target` looks up `target_conversation_id`
through the owner-scoped `ConversationRepository` (`creator_user_id =
owner_user_id`); if that lookup returns nothing the target is missing or not the
owner's, so the occurrence is recorded `failed` and no `start_run` happens. This
re-check at dispatch backstops the create-time check (the conversation may have
been deleted or its ownership changed in between). For `new_each_run`, the fresh
conversation is created with `creator_user_id = owner_user_id`, so it is owned by
the run identity by construction.

`resolve_target` and `RunContext` assembly are the only scheduled-task-specific
bits; everything downstream (tools, memory, cost, tracing, sandbox) is the
existing run machinery. #152 (generic triggers) and #153 (managed agents) reuse
the same `start_run` call with their own trigger source; only the "what decided
to run and with what prompt/identity" differs.

### Run history / observability

- `scheduled_task_runs` is the durable history (above). The workspace API
  exposes it read-only per task.
- Each fire's `run_id` links to the existing run event stream / cost records, so
  a scheduled run is inspectable exactly like an interactive one (same
  `CostMiddleware` billing, same tracer spans stamped with conversation/user/
  org/workspace).
- Crash recovery is the re-claim path (above), not the reaper: a stale `claimed`
  row is re-dispatched, giving at-least-once delivery. The reaper only bounds it —
  it flips `claimed` rows whose `claim_count` hit `max_claims` to `failed` so a
  repeatedly-failing occurrence is visible and stops being retried, rather than
  looping forever.

### Scope-isolated workspace routes

New router `ws_scheduled_tasks.py` under
`backend/cubeplex/api/routes/v1/`, prefix `/ws/{workspace_id}/scheduled-tasks`,
guarded by `require_member` — mirrors the existing `ws_*` routers. Reads need
membership; mutations additionally need owner-or-admin (see Identity / permission
below). Endpoints:

- `POST   /ws/{ws}/scheduled-tasks` — create (member).
- `GET    /ws/{ws}/scheduled-tasks` — list (member).
- `GET    /ws/{ws}/scheduled-tasks/{id}` — detail (member).
- `PATCH  /ws/{ws}/scheduled-tasks/{id}` — edit schedule/prompt/target (owner or admin).
- `POST   /ws/{ws}/scheduled-tasks/{id}/pause` / `.../resume` (owner or admin).
- `DELETE /ws/{ws}/scheduled-tasks/{id}` — soft delete (owner or admin); stamps
  `deleted_at` only, so the poller's `deleted_at IS NULL` filter is what stops
  future fires.
- `GET    /ws/{ws}/scheduled-tasks/{id}/runs` — run history (member).

Persistence goes through a `ScheduledTaskRepository(ScopedRepository)` so
`(org_id, workspace_id)` scoping is structural, not bolted-on ACL. No
admin/cross-workspace variant in v1; if one is ever needed it gets its own
`/api/v1/admin/...` handler family, never a `?scope=` param (per CLAUDE.md
scope-isolation rule). Frontend gets its own Next route + page file assembling
list / editor / run-history modules.

### Relationship with triggers (#152) and managed agents (#153)

- A scheduled task is one concrete *trigger kind* under #152's umbrella. #152
  generalizes "an external condition (time / webhook / IM message) decided to
  start an agent run." We implement the time kind now and expose the shared
  `dispatch_scheduled_run`-style seam so #152 can lift it to
  `dispatch_triggered_run(trigger_source, prompt, identity, target)` without
  reworking the run-start path.
- Managed agents (#153) need the same "start a run with a persisted identity,
  no live HTTP request" capability. They reuse the dispatch seam; the
  difference is #153 runs an ongoing/looping agent, while a scheduled task fires
  a single bounded run per occurrence. Keep the dispatch function returning a
  `run_id` so both can build on it.
- IM (#149) is another inbound source that ends in the same `start_run`; out of
  scope here beyond noting the shared seam.

### Identity / permission the task runs as

- A scheduled run executes as `owner_user_id` — the member who created the task
  (captured from `RequestContext` at create time). All run machinery
  (tools, MCP grants, memory, cost) keys off `RunContext(user_id, org_id,
  workspace_id)`, so the run sees exactly what that user would see interactively.
- RBAC: *creating* a scheduled task requires workspace membership
  (`require_member`); the creator becomes `owner_user_id`. *Mutating* an existing
  task — edit (schedule/prompt/target), pause/resume, delete — requires being
  the task's owner **or** a workspace admin. List/detail/run-history reads stay
  at plain membership. The reason mutation is owner-or-admin and not any member:
  a scheduled run executes as `owner_user_id` and inherits that user's tools, MCP
  grants, memory, and sandbox, so letting any member rewrite another member's
  prompt or target would let them run arbitrary instructions under the owner's
  identity. The check lives in the route handler (membership + ownership /
  admin-role) and is enforced again at the repository layer through the
  `(org_id, workspace_id)` scope; v1 has no ownership-reassignment flow, so an
  admin can pause/delete a task but not retarget its run identity to themselves.
- If the owner later loses membership, the poller must skip the task (record
  `failed` / auto-pause) rather than run as a removed user — checked at fire time
  against current membership.
- Sandbox ownership (#144): the existing model is one running sandbox per
  `(user_id, workspace_id)` (`user_sandbox.py`). A scheduled run reuses that
  user's workspace sandbox, same as an interactive run for that user. The
  scheduler **does not serialize** concurrent fires for the same user — the
  sandbox is treated as the agent's shared computer, and concurrent activity
  in one computer is normal. Collision-avoidance (cwd, ports, env, files,
  browser session, stdio MCP single-client) is the task author's and the
  agent's responsibility, not the scheduler's. The #145 sandbox lease remains
  a **non-exclusive delay-pause timestamp**, not a mutex; scheduled runs may
  fire while another run holds a lease. Two real edge cases — the browser
  tool's single-instance constraint and stdio-MCP single-client servers —
  are acknowledged v1-known: contention surfaces as a fail-fast at the tool
  layer rather than as scheduler-level mitigation. v1 does not add per-task
  isolated sandboxes (open question on whether managed agents (#153) will
  need that).

### v1 scope

In: the data model, the poller with `SKIP LOCKED` claim + occurrence-idempotency
key, the three schedule kinds, fixed/new-each-run targets, run-latest-once
missed-run policy, run history, workspace CRUD + pause/resume routes + page,
member-read / owner-or-admin-mutate authorization, membership-checked owner
identity, reuse of `start_run`. Out: everything in the
Non-goals list (admin surface, backfill, sub-minute, generic triggers, managed-
agent loop, per-task sandbox isolation).

---

## Testing strategy (E2E-first)

Per CLAUDE.md, E2E is the priority; fall back to unit only where the system
genuinely can't be simulated.

- **E2E (primary):** create a `once` task scheduled a few seconds out via the
  workspace API, advance/await, assert a run started in the target conversation
  and a `scheduled_task_runs` row records `started` with a real `run_id`. Cover
  `interval` (fires twice), pause-before-fire (no run), resume, delete. Use a
  short `misfire_grace` and a fast poll interval in the test config so tests
  don't wait minutes. Run against the worktree's per-slot DB/Redis.
- **Concurrency E2E / integration:** drive two pollers (or two `SELECT … FOR
  UPDATE SKIP LOCKED` claims) against one due task and assert exactly one
  `scheduled_task_runs` row exists (the unique `(task_id, scheduled_for)`
  constraint), proving no double-fire across replicas.
- **Missed-run integration:** set `next_fire_at` in the past beyond grace,
  poll, assert `skipped_missed` recorded and `next_fire_at` fast-forwarded to a
  future occurrence — no backfill storm.
- **Unit:** `next_fire_at` computation for cron (incl. tz/DST boundaries) and
  interval, and the missed-vs-fire grace decision. These are pure functions and
  worth isolating, but the firing path itself is covered E2E.

---

## Open Questions

1. **Poll interval vs latency.** A 15–30s jittered poll means a task scheduled
   for 9:00:00 may fire at 9:00:20. Acceptable for v1 minute-granularity
   schedules? Or do we want a tighter loop / a Redis-sorted-set "next wake"
   hint to fire closer to the second?

   **Resolved 2026-05-28: 15s poll interval with jitter, minute-granularity
   v1.** The Redis sorted-set "next wake" hint is deferred to v2; the latency
   penalty of polling is acceptable at minute granularity.

2. **Missed-run policy default.** Is "run-latest-once, skip the rest" the right
   default, or do some users expect every missed daily report to eventually
   run? Should the policy be a per-task setting (`skip` | `run_latest` |
   `run_all` capped) rather than a global one?

   **Resolved 2026-05-28: per-task setting, default `run_latest`.** Allowed
   values: `skip` / `run_latest` / `run_all`. The `run_all` value is capped at
   a sane backfill bound by the same `latest_due` arithmetic — no per-slot
   write storm; `run_all` records each slot as a distinct occurrence up to the
   bound. Default stays `run_latest` so existing behavior carries over.

3. **`fixed` target + busy conversation.** Is `skipped_active` (drop the
   occurrence) correct, or should it queue and run when the conversation frees
   up? Queuing reintroduces backlog/ordering complexity we deliberately avoided.

   **Resolved 2026-05-28 (CHANGE from earlier draft): postpone by 5 minutes,
   retry up to 3 times, then mark `skipped_busy_max_retries`.** Earlier drafts
   had the occurrence dropped immediately as `skipped_active`. New behavior:
   on busy, set `next_retry_at = now + 5m` and leave the row re-claimable;
   increment `retry_count` on each retry. After 3 retries that still hit a
   busy conversation, mark the occurrence terminal `skipped_busy_max_retries`
   and move on. No global queue is introduced — the retry state lives on the
   occurrence row itself.

4. **Owner-left-workspace behavior + ownership reassignment.** Auto-pause the
   task, hard-fail each fire, or allow a workspace admin to reassign ownership?
   v1 lets an admin pause/delete a task but deliberately has *no* reassignment
   flow (an admin can't retarget a task's run identity to themselves), because
   reassigning would silently change which user's tools/MCP/memory/sandbox the
   runs inherit. If reassignment is wanted later it needs explicit
   confirmation-of-identity semantics. Ties into whether tasks are truly
   user-owned or workspace-owned-with-a-runner-identity.

   **Resolved 2026-05-28: auto-pause the task when the owner leaves the
   workspace; no reassignment flow in v1.** The poller's owner-membership
   check at dispatch flips the task to `paused` (instead of failing each
   subsequent fire), so history doesn't fill with `failed` rows for a task
   whose identity is gone. Reassignment stays a non-goal.

5. **Concurrent fires into one user's single sandbox (#144).** Two tasks for the
   same user firing at the same minute share one workspace sandbox — is that
   acceptable, or does scheduled/managed-agent work need its own sandbox
   identity? This likely must be resolved jointly with #153.

   **Resolved 2026-05-28 (CHANGE from earlier draft): no scheduler-level
   serialization; concurrent dispatch into one user's sandbox is allowed.**
   Earlier drafts implied the scheduler would serialize per-user fires (or
   record `skipped_active` to avoid them). New stance: a sandbox is
   conceptually a computer; concurrent activity in one computer is normal
   and is the agent / task author's responsibility to handle (cwd, ports,
   env, files, browser, stdio-MCP). The #145 sandbox lease remains a
   non-exclusive **delay-pause** timestamp, NOT a mutex — scheduled fires
   may proceed while another run holds a lease. The two real edge cases
   (single-instance browser, single-client stdio MCP servers) are
   acknowledged v1-known and fail-fast at the tool layer; the scheduler
   does not mitigate them.

6. **Re-claim duplicate window.** The re-claim path gives at-least-once: a stale
   `claimed` row is re-dispatched after `claim_timeout`. The unavoidable
   duplicate window is a replica that *did* call `start_run` (a run is live) but
   died before writing `run_id`, so a later poller re-claims and starts a second
   run for the same occurrence. Is that rare double-fire acceptable for v1, or do
   we want a tighter signal (e.g. mark `started` *before* `start_run` returns and
   reconcile via the run event stream) to shrink it further? What are the right
   `claim_timeout` / `max_claims` defaults?

   **Resolved 2026-05-28: accept the rare double-fire from the at-least-once
   window.** Defaults: `claim_timeout = 2 min`, `max_claims = 3`. The
   tighter-signal reconciliation is deferred; the duplicate window is small
   enough at these defaults to be acceptable in v1.

7. **Shared trigger seam shape (#152).** Is `dispatch_*_run(...) -> run_id` the
   right contract, or should #152 define a richer `TriggerEvent` envelope now so
   scheduled tasks emit it from day one rather than being retrofitted?

   **Resolved 2026-05-28: v1 dispatch contract is the simple
   `dispatch_*_run(...) -> run_id` form. No `TriggerEvent` envelope upfront.**
   #152 will retrofit a richer envelope when generic triggers land; carrying
   it now would be speculative.

8. **Per-task run limits / cost guardrails.** Should a runaway recurring task
   (e.g. every minute, expensive model) have a per-task budget or rate cap,
   given runs bill through `CostMiddleware` with no human in the loop?

   **Resolved 2026-05-28 (CHANGE from earlier draft): out of scope for #150
   v1; cost protection is the project-wide `CostMiddleware`'s job.** Earlier
   drafts considered a per-task `max_runs_per_day` cap (default 100) in this
   feature. New stance: scheduled tasks are just another caller of the run
   path; cost guardrails belong in the cost system that already meters every
   run, not duplicated per-feature. The cost system will land separately.

---

## References

- `backend/cubeplex/streams/run_manager.py` — `RunManager.start_run`,
  `RunContext`, in-process run task model.
- `backend/cubeplex/api/routes/v1/conversations.py` — `send_message`, the sole
  current run-start caller.
- `backend/cubeplex/api/app.py` — lifespan; where `RunManager` and the poller
  start; multi-replica startup.
- `backend/docs/deploy-k8s-graceful-restart.md` — multi-replica, rolling
  restart, drain.
- `docs/dev/specs/2026-05-25-multi-instance-run-control-design.md` —
  existing cross-replica run-control (Redis pub/sub) pattern.
- `backend/cubeplex/models/mixins.py`, `backend/cubeplex/models/public_id.py` —
  `CubeplexBase` / `OrgScopedMixin` / `_PREFIX` public-id convention.
- `backend/cubeplex/models/conversation.py`, `user_sandbox.py` — model + soft-
  delete + sandbox-ownership patterns to mirror.
- APScheduler 4.0 user guide / migration (multiple schedulers sharing a Postgres
  datastore, `misfire_grace_time`):
  https://apscheduler.readthedocs.io/en/master/userguide.html ,
  https://apscheduler.readthedocs.io/en/master/migration.html ,
  https://github.com/agronholm/apscheduler/issues/465
- APScheduler vs Celery Beat tradeoffs:
  https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-vs-celery-beat ,
  https://stackshare.io/stackups/apscheduler-vs-celery
- Postgres `SELECT … FOR UPDATE SKIP LOCKED` job-queue pattern:
  https://vladmihalcea.com/database-job-queue-skip-locked/ ,
  https://www.dbpro.app/blog/postgresql-skip-locked ,
  https://www.netdata.cloud/academy/update-skip-locked/
