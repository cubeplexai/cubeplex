# Plan 3 — Auto-background, Monitor, conversation lifetime

**Goal:** A long foreground command becomes a tracked background handle
after 15s; the agent can watch predicates without polling; servers and
monitors stay attached to the conversation after `DoneEvent`.

**Architecture:** Same `start` / `poll` / `kill` and `sandbox_commands`
as plan 2. Foreground wait budget shrinks to 15s (except `sleep`).
`lifetime=conversation` rows are not killed on `DoneEvent`. Monitor is
`start()` plus rate-limited line wakes through a durable outbox. Wakes
after the starting run has ended go through steering or a new
`start_run` with stored `started_by_user_id`. Sandbox panel lists
running rows; user Kill is the workspace `kill_execute` path.
Sandbox pause/restart/kill reconciles command rows.

**Tech stack:** Same as plan 2 · existing `steering_delivery` ·
`RunManager.start_run` · sandbox side panel.

**Spec:** Phase 3 of
`docs/dev/specs/2026-09-18-sandbox-background-execute-design.md`.
Depends on plan 2.

**Worktree:** same branch.

---

## Unit 1 — Auto-background

**Files**

- `backend/cubeplex/middleware/sandbox.py` — foreground wait cap 15s
  unless first token is `sleep`. The cap slot is reserved (`starting`)
  **before** `start()`, same as plan 2. The tool keeps the fenced
  lease during the 15s wait (plan 2). Still-running at 15s →
  `running`, hand `owner_id` to the coordinator, return
  `background=true` details. Exit inside 15s → normal foreground
  result; the tool CAS-deletes/finalizes the reservation.
- `backend/cubeplex/prompts/sandbox.py` — long commands may omit
  `background=true`; do not `sleep` to wait.

**Interfaces**

Fixed 15s block budget, not model-settable. `timeout_seconds` remains
the **CubePlex** kill deadline only while the wait is still foreground
(`poll` + `kill`). `start()` still has no provider timeout (plan 2),
so auto-bg does not inherit a 120s OpenSandbox deadline. After
auto-background, that CubePlex kill deadline no longer applies;
lifetime is `run` (notify true) per the spec mapping.

Explicit `background=true` + `notify_on_complete=false` still wins
(conversation lifetime, Unit 2).

**Core logic**

First token `sleep` → no auto-bg; wait as today until kill timeout.
`sleep 5 && make` is still auto-bg’d (first token is not a bare
`sleep` wait — spec says “whose first token is `sleep`”). Bare
`sleep 30` is the excluded case.

**Tests**

- Fake process still running at 16s: tool returns `scmd-` and
  `status=running`; process not killed.
- Same process exiting at 2s: foreground result, no row.
- `sleep 30` without `background=true`: still blocked, not auto-bg’d.

---

## Unit 2 — `lifetime` column and run-end policy

**Files**

- Model + Alembic autogenerate: `kind` (`execute` | `monitor`),
  `lifetime` (`run` | `conversation`), `notify_run_id`, plus monitor
  counters: `wake_count`, `wake_drops`, `line_wakes_disabled`,
  `flood_started_at`, `monitor_deadline_at`.
- Index `(conversation_id, status)`. Update counters in the same CAS
  as `log_cursor` / outbox insert. Takeover continues those values.
- `on_run_end` / run-end kill: only `lifetime=run`.
- Stale recovery: same. Conversation rows stay; coordinator keeps
  the lease.

**Interfaces**

Mapping from spec:

| Start | lifetime | notify |
|---|---|---|
| Foreground exits ≤15s | no row | — |
| Auto-bg or `background=true` notify true | `run` | true |
| `notify_on_complete=false` | `conversation` | false |
| `monitor` | `conversation` | line + exit wakes |

**Core logic**

`DoneEvent` with only conversation-scoped rows remaining does not kill
them. `in_use_until` stays renewed while any `running` row exists.

**Tests**

- Notify-false server still `running` after a completed run.
- Stale of that `run_id` does not kill the server row.

---

## Unit 3 — `monitor` tool and rate limits

**Files**

- `backend/cubeplex/middleware/sandbox.py` (or a sibling factory) —
  `monitor(command, description, persistent=false, timeout_seconds=3600)`.
- Coordinator: per-row line cursor, rate-limit counters, promotion to
  exit-only, 30s flood kill.
- Prompt section for monitor discipline.

**Interfaces**

`persistent=true` → no timeout, until `kill_execute` or sandbox death.
Else kill after `timeout_seconds` (default 3600, max 36000).
Each stdout line is a candidate wake; process exit is always a wake.
`kind=monitor`. `kill_execute` works on monitors.

Rate limit (per monitor):

- At most one delivered line-wake / 15s.
- 3 consecutive dropped wakes **or** 8 delivered line-wakes lifetime
  → disable line wakes; keep exit wake.
- Continuous 15s-floor violations for 30s → kill.

**Core logic**

Do not use monitor to wait on a build. Wakes go to the outbox (Unit 4),
not a second CubeLoop tool result.

**Tests**

- One `FAILED` line → one pending wake.
- One line per second for 20s → not 20 wakes; after promotion, only
  exit remains (or kill if flood 30s — use a faster fake clock in unit
  tests).
- Coordinator takeover mid-monitor continues `wake_count` /
  `wake_drops` / deadline; does not reset the 8-wake cap.

---

## Unit 4 — Durable outbox and follow-up run

**Files**

- New table or columns for wakes: unique id (command_id + seq or
  `exit`), `state` `pending` | `claimed` | `delivered`.
- Coordinator delivery (Redis `running` is not enough to mark
  delivered):
  - Steer only after durable steering returns a queued/committed
    receipt; keep the outbox `pending` until that checkpoint ack.
    While CubeLoop is blocked in `on_run_end`, `submit_input` may
    fail — leave the wake `pending`, or let the sandbox `on_run_end`
    hook consume eligible wakes for this `run_id` directly.
  - `paused_hitl` → leave `pending`; do not `start_run`.
  - else `start_run` with `started_by_user_id`, same conversation
    (scheduled-task fixed destination). Re-check membership; gone →
    404 / drop. One-active-run conflict → retry, never a second run.
- `RunContext` fields persisted or resolved from
  `started_by_user_id` + conversation (topic/group as today’s
  scheduled-task start does).

**Interfaces**

Wake payload: unique `wake_id`, command id, description, reason
(`line` | `exit`), text tail. Injected message metadata uses
`notice_id = wake_id` (not `command_id` — a monitor has many wakes).
Reconcile that exact outbox row.

**Core logic**

Outbox claim has `owner_id` + expiry (same fencing as commands).
Pre-generate the intended `run_id` / steer id, persist it on the
outbox row **before** `start_run`/steer, and pass that stable id into
admission. Expired `claimed` rows reconcile against Redis run meta,
steering rows, and checkpoint history before retry — do not blindly
reset to `pending` (that double-fires after a successful
`start_run`). HITL pause blocks a new run. Do not bypass membership
with a system actor.

**Tests** (`tests/e2e/`)

- Wake with no active run → one new run, `notify_run_id` set,
  outbox `delivered`.
- Wake while a run is `running` and steering accepts the claim →
  steer, no second run; rejected submit leaves outbox `pending`.
- Wake while `paused_hitl` → outbox stays `pending`.
- Two coordinators cannot double-deliver the same wake id.

---

## Unit 5 — Sandbox panel list and user Kill

**Files**

- Workspace route under `/api/v1/ws/{workspace_id}/...` listing
  `running` commands for the conversation’s sandbox; POST kill that
  shares `kill_execute` lookup rules. No admin route, no `?scope=`.
- `frontend/packages/web/components/panel/sandbox/` — list above or
  on the Terminal tab: description, elapsed, Kill.
- Refresh loads this API, not the dead run’s SSE.

**Interfaces**

```
GET  .../conversations/{id}/sandbox-commands  → [{id, description, started_at, status}]
POST .../conversations/{id}/sandbox-commands/{command_id}/kill
```

404 on org/workspace/conversation/sandbox mismatch.

**Tests**

- Frontend: running row renders; Kill calls POST.
- Backend e2e: member can kill own conversation command; other
  conversation’s `scmd-` 404s.

---

## Unit 6 — Sandbox lifecycle reconcile

**Files**

- `backend/cubeplex/sandbox/manager.py` — `_kill_record`,
  `pause_idle` (pause ≡ kill for processes), user restart/delete:
  mark associated `starting` **and** `running` rows `killed`, clear
  `provider_ref`, enqueue an exit wake if one is still due. Late
  `start()` must CAS `starting → running` and interrupt on failure.

**Core logic**

OpenSandbox pause does not keep live processes. Idle pause is usually
blocked by `in_use_until`; manual restart/kill is the path that
destroys processes. Do not leave UI `running` after the container is
gone.

**Tests**

- Restart sandbox → no `running` command rows for that
  `user_sandbox_id`.

---

## Unit 7 — Docs

**Files**

- `docs/site` sandbox guide — servers can outlive the turn; Kill in
  the sandbox panel; monitor is for predicates, not builds.

---

## Out of this PR

E2B / Daytona drivers. Processes that survive sandbox pause/kill.
Generic jobs for non-shell tools. Changing the 120s/1800s kill
timeout (this PR only adds the 15s **block** budget).
