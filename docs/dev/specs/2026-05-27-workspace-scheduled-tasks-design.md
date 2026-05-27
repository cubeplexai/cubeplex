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
(`backend/cubebox/streams/run_manager.py`). It is called from the HTTP handler
`send_message` in `backend/cubebox/api/routes/v1/conversations.py`. The flow:

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

There is **none today**. Grepping `backend/cubebox` for
scheduler/cron/celery/apscheduler/recurring/background_task returns nothing. The
only background execution is the per-run asyncio task inside `RunManager`, plus
memory-consolidation tasks it tracks. Redis is used heavily (run event streams,
active-run locks, pub/sub control, OAuth token cache) but never as a job queue.

> Note: `backend/.claude/scheduled_tasks.lock` is unrelated — it is a stray
> Claude Code session lock file (`{"sessionId":...,"pid":...}`), not part of any
> cubebox feature. It can be ignored / gitignored.

### Deployment topology

Multi-replica. `backend/docs/deploy-k8s-graceful-restart.md` documents rolling
restarts with `terminationGracePeriodSeconds`, split liveness/readiness probes,
and in-flight run draining on SIGTERM. So we must assume **N replicas of the
same FastAPI process**, each with its own `RunManager`, all sharing one Postgres
and one Redis. There is no existing leader-election primitive. Any scheduler we
add must therefore either elect a leader or be safe to run concurrently on every
replica.

### Public-ID + model conventions

New business tables subclass `CubeboxBase` (+ `OrgScopedMixin` for
org/workspace scoping) and set a `_PREFIX` ClassVar; the PK auto-fills via
`generate_public_id(_PREFIX)` in `model_post_init`
(`backend/cubebox/models/mixins.py`, `public_id.py`). Shared prefixes used by
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

New table `scheduled_tasks` (`CubeboxBase` + `OrgScopedMixin`, `_PREFIX =
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
  - `target_conversation_id` (nullable FK) — required when `fixed`.
- Scheduler bookkeeping:
  - `next_fire_at` (UTC, indexed) — the next occurrence the poller will claim.
    NULL once a `once` task has fired or a task is paused.
  - `last_fired_at` (UTC, nullable).
- Index on `(status, next_fire_at)` for the poller's hot query; partial index
  on `deleted_at IS NOT NULL` for GC (mirrors conversations).

New table `scheduled_task_runs` (history; `CubeboxBase` + `OrgScopedMixin`,
`_PREFIX = "stkrn"`):

- `scheduled_task_id` (FK), `org_id`, `workspace_id`.
- `scheduled_for` (UTC) — the occurrence time this row represents.
- `fired_at` (UTC) — when the poller actually claimed/started it.
- `outcome` — `started` | `skipped_missed` | `skipped_active` | `failed`.
- `run_id` (nullable) — the `RunManager` run id, when one started.
- `conversation_id` (nullable) — where it ran.
- `detail` (nullable text) — error / skip reason.
- **Unique constraint `(scheduled_task_id, scheduled_for)`** — the
  occurrence-idempotency key. Inserting this history row is the act that claims
  an occurrence; a duplicate insert violates the constraint and the second
  attempt knows the occurrence is already handled. This is the backstop that
  makes a double-claim (Option C's rare race) produce one run, not two.

### Scheduler component

A new `ScheduledTaskPoller`, started in the FastAPI lifespan
(`backend/cubebox/api/app.py`) on **every** replica, alongside `RunManager`.

Loop (every ~15–30s, jittered to avoid replica thundering-herd):

1. `BEGIN`.
2. `SELECT * FROM scheduled_tasks WHERE status='active' AND next_fire_at <=
   now() ORDER BY next_fire_at FOR UPDATE SKIP LOCKED LIMIT k`. Other replicas
   skip these locked rows.
3. For each claimed task:
   - Determine the occurrence time `scheduled_for` (= the `next_fire_at` we
     read).
   - Apply **missed-run policy** (below) to decide fire vs skip.
   - Insert a `scheduled_task_runs` row keyed `(task_id, scheduled_for)`. If the
     unique constraint trips, another path already handled this occurrence —
     skip.
   - Recompute and write the task's next `next_fire_at` (cron → next match in
     tz; interval → `scheduled_for + interval`; once → NULL + status stays
     active but with no next fire). Do this **in the same transaction** so the
     row's lock + next-fire advance commit atomically.
4. `COMMIT`. Releasing the lock with `next_fire_at` already advanced means no
   other replica can re-claim this occurrence.
5. **After commit**, for each row marked `started`, call
   `run_manager.start_run(...)` on this replica with a reconstructed
   `RunContext` and the resolved target conversation. The actual run then lives
   as a normal in-process task on this replica, identical to an interactive run.

Why fire after commit, not inside the transaction: `start_run` does Redis +
async work and may run for a long time; holding a Postgres row lock across it
would be wrong. The history row (committed in step 3) is the durable record
that the occurrence was claimed; if this replica dies between commit and
`start_run`, the history row shows `started` with a null `run_id`, which a
reaper can detect and the next poll can optionally retry once (bounded).

#### Missed-run policy (v1)

When the poller claims a row whose `next_fire_at` is in the past:

- If `now() - scheduled_for <= misfire_grace` (default 5 min, configurable):
  fire normally.
- If `now() - scheduled_for > misfire_grace`: this occurrence was missed (system
  down / paused-then-resumed across the boundary). v1 policy is
  **run-latest-once**: record `skipped_missed` for the stale occurrence, fast-
  forward `next_fire_at` to the *next* future occurrence, and fire at most the
  single most-recent due occurrence if it is within grace. No backfill of the
  full missed series. This is recorded in history so it is observable.

`once` tasks past grace are recorded `skipped_missed` and never fire.

#### One-run-per-conversation interaction

`start_run` rejects a second run on a conversation that already has a `running`
run. For `target_mode=fixed`, if the conversation is busy at fire time, the
poller records `skipped_active` (don't queue/stack runs). For
`target_mode=new_each_run`, a fresh conversation is created so there is never a
collision — this is the recommended default for recurring tasks.

### Run dispatch — reuse the existing path

Dispatch is deliberately thin: resolve/create the target conversation, build a
`RunContext(user_id=owner_user_id, org_id, workspace_id)`, then call the **same**
`run_manager.start_run(...)` interactive runs use. The shared seam (for #152 /
#153) is a small `run_dispatch` service:

```
dispatch_scheduled_run(task, occurrence) ->
    conversation = resolve_target(task)          # fixed or create new
    ctx = RunContext(task.owner_user_id, task.org_id, task.workspace_id)
    run_id = run_manager.start_run(conversation_id, task.prompt, ctx=ctx)
    return run_id
```

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
- A reaper (piggybacks on the poller) flips `started`-with-null-`run_id` rows
  older than a threshold to `failed` so a replica crash between commit and
  `start_run` is visible, not silently lost.

### Scope-isolated workspace routes

New router `ws_scheduled_tasks.py` under
`backend/cubebox/api/routes/v1/`, prefix `/ws/{workspace_id}/scheduled-tasks`,
guarded by `require_member` — mirrors the existing `ws_*` routers. Endpoints:

- `POST   /ws/{ws}/scheduled-tasks` — create.
- `GET    /ws/{ws}/scheduled-tasks` — list.
- `GET    /ws/{ws}/scheduled-tasks/{id}` — detail.
- `PATCH  /ws/{ws}/scheduled-tasks/{id}` — edit schedule/prompt/target.
- `POST   /ws/{ws}/scheduled-tasks/{id}/pause` / `.../resume`.
- `DELETE /ws/{ws}/scheduled-tasks/{id}` — soft delete.
- `GET    /ws/{ws}/scheduled-tasks/{id}/runs` — run history.

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
- RBAC: creating/editing a scheduled task requires workspace membership
  (`require_member`); v1 does not add a separate finer permission. If the owner
  later loses membership, the poller must skip the task (record `failed` /
  auto-pause) rather than run as a removed user — checked at fire time against
  current membership.
- Sandbox ownership (#144): the existing model is one running sandbox per
  `(user_id, workspace_id)` (`user_sandbox.py`). A scheduled run reuses that
  user's workspace sandbox, same as an interactive run for that user. Two
  scheduled tasks owned by the same user that fire concurrently into the same
  sandbox is the same contention an interactive user already has; v1 does not
  add per-task isolated sandboxes (open question on whether managed agents
  (#153) will need that).

### v1 scope

In: the data model, the poller with `SKIP LOCKED` claim + occurrence-idempotency
key, the three schedule kinds, fixed/new-each-run targets, run-latest-once
missed-run policy, run history, workspace CRUD + pause/resume routes + page,
membership-checked owner identity, reuse of `start_run`. Out: everything in the
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
2. **Missed-run policy default.** Is "run-latest-once, skip the rest" the right
   default, or do some users expect every missed daily report to eventually
   run? Should the policy be a per-task setting (`skip` | `run_latest` |
   `run_all` capped) rather than a global one?
3. **`fixed` target + busy conversation.** Is `skipped_active` (drop the
   occurrence) correct, or should it queue and run when the conversation frees
   up? Queuing reintroduces backlog/ordering complexity we deliberately avoided.
4. **Owner-left-workspace behavior.** Auto-pause the task, hard-fail each fire,
   or allow a workspace admin to reassign ownership? Ties into whether tasks are
   truly user-owned or workspace-owned-with-a-runner-identity.
5. **Concurrent fires into one user's single sandbox (#144).** Two tasks for the
   same user firing at the same minute share one workspace sandbox — is that
   acceptable, or does scheduled/managed-agent work need its own sandbox
   identity? This likely must be resolved jointly with #153.
6. **Reaper retry semantics.** When a replica dies between history-commit and
   `start_run`, do we retry that occurrence once on the next poll, or just mark
   it `failed`? Retry risks a late duplicate; no-retry risks a silently dropped
   scheduled report.
7. **Shared trigger seam shape (#152).** Is `dispatch_*_run(...) -> run_id` the
   right contract, or should #152 define a richer `TriggerEvent` envelope now so
   scheduled tasks emit it from day one rather than being retrofitted?
8. **Per-task run limits / cost guardrails.** Should a runaway recurring task
   (e.g. every minute, expensive model) have a per-task budget or rate cap,
   given runs bill through `CostMiddleware` with no human in the loop?

---

## References

- `backend/cubebox/streams/run_manager.py` — `RunManager.start_run`,
  `RunContext`, in-process run task model.
- `backend/cubebox/api/routes/v1/conversations.py` — `send_message`, the sole
  current run-start caller.
- `backend/cubebox/api/app.py` — lifespan; where `RunManager` and the poller
  start; multi-replica startup.
- `backend/docs/deploy-k8s-graceful-restart.md` — multi-replica, rolling
  restart, drain.
- `docs/dev/specs/2026-05-25-multi-instance-run-control-design.md` —
  existing cross-replica run-control (Redis pub/sub) pattern.
- `backend/cubebox/models/mixins.py`, `backend/cubebox/models/public_id.py` —
  `CubeboxBase` / `OrgScopedMixin` / `_PREFIX` public-id convention.
- `backend/cubebox/models/conversation.py`, `user_sandbox.py` — model + soft-
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
