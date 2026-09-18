# Plan 2 — Managed in-run background commands

**Goal:** The agent can start a long sandbox command, keep working in the
same turn, and be told when it exits — without leaking OpenSandbox ids
and without holding `DoneEvent` after CubeLoop has already stopped.

**Architecture:** Agent `execute` uses driver `start` + wait (foreground)
or `start` + return (background). CubePlex owns `scmd-` ids in
`sandbox_commands`. A coordinator (next to the sandbox cleanup loop)
leases rows, polls the provider, renews `in_use_until`, and records
exit. In-run notify is CubeLoop `on_run_end` injecting a user message
**before** `AgentEndEvent`. Host heartbeat stays up during that wait.
HITL pause still emits `DoneEvent(paused=true)`. Run end / cancel /
stale recovery kill leftover rows for that `run_id`. Chip overlay reads
the table; completion is not a second CubeLoop tool result.

**Tech stack:** Alembic · `Sandbox` ABC · OpenSandbox detached commands ·
LocalSandbox subprocess · CubeLoop `on_run_end` · Redis run heartbeat ·
existing workspace SSE.

**Spec:** Phase 2 of
`docs/dev/specs/2026-09-18-sandbox-background-execute-design.md`.
Depends on plan 1 (streaming + chip `status=running`). Plan 3 adds
auto-bg, monitor, and conversation lifetime.

**Worktree:** same as plan 1. Stay on
`feat/2026-09-18-sandbox-background-execute`.

---

## Unit 1 — Driver `start` / `poll` / `kill`

**Files**

- `backend/cubeplex/sandbox/base.py` — `supports_background()`,
  `ProcessHandle`, `ProcessSnapshot`, `start` / `poll` / `kill`.
- `backend/cubeplex/sandbox/opensandbox.py` — detached command API;
  `provider_ref` is the OpenSandbox command id.
- `backend/cubeplex/sandbox/local.py` — asyncio subprocess; `provider_ref`
  is local pid / handle.
- `backend/cubeplex/sandbox/lazy.py` — forward the three methods; keepalive
  while the caller waits on a foreground `start`.

Infra helpers (`start_browser`, skills sync) keep blocking `execute()`.

**Interfaces**

```
class ProcessHandle:
    command_id: str          # assigned by CubePlex, empty until the tool fills it
    provider_ref: str        # driver-private

class ProcessSnapshot:
    status: Literal["running", "exited", "killed"]
    exit_code: int | None
    new_output: str          # since last poll

supports_background() -> bool  # default False
async start(command, *, timeout=None, envs=None, as_root=False, on_chunk=None) -> ProcessHandle
async poll(handle) -> ProcessSnapshot
async kill(handle) -> None
```

`start().command_id` may be blank; the tool writes the `scmd-` id on the
handle after insert. Drivers must not put `provider_ref` in `on_chunk`
text.

Foreground wait lives in the tool (poll until exit / CubePlex kill
deadline), not in the driver. Agent-facing `start()` does **not** pass a
provider timeout (OpenSandbox would keep it after phase-3 auto-bg).
Bare `sleep` may pass a provider timeout because it is never
auto-promoted. The tool still enforces 120 / 1800 by `poll` + `kill`.

**Core logic**

OpenSandbox: `background=True`. After `run()` returns, the start SSE is
done. Further output is `get_background_command_logs` via `poll()`;
the wait loop forwards `new_output` to `on_chunk` (plan 1 contract
unchanged). Persist `log_cursor` on the row. There is no
promote-foreground API. `supports_background() == False` must not
spawn `cmd &`.

**Tests** (`backend/tests/unit/` against LocalSandbox)

- `start` returns before a `sleep 2` exits; `poll` later sees `exited`.
- `kill` then `poll` → `killed`.
- `on_chunk` fires during a slow print loop.

---

## Unit 2 — Table and repository

**Files**

- `backend/cubeplex/models/public_id.py` — `PREFIX_SANDBOX_COMMAND = "scmd"`.
- `backend/cubeplex/models/sandbox_command.py` — org-scoped table.
- `backend/cubeplex/models/__init__.py` — export.
- `backend/cubeplex/repositories/sandbox_command.py` — `ScopedRepository`.
- Alembic autogenerate only (`alembic revision --autogenerate`).

**Interfaces** (phase-2 columns only)

`id`, `org_id`, `workspace_id`, `user_sandbox_id`, `conversation_id`,
`run_id`, `tool_call_id`, `started_by_user_id`, `agent_id` (nullable;
null = main agent), `command`, `description`, `provider`,
`provider_ref` (null until `start()` returns), `status`
(`starting` / `running` / `exited` / `killed`),
`notify_on_complete`, `notice_state` (`none` / `pending` /
`delivered`), `log_path`, `log_cursor` (driver-private int/text, not
in tool/SSE), `exit_code`, `finished_at`, `owner_id`, `owner_until`.

Indexes: `(user_sandbox_id, status)`, `(run_id, status)`.
Do not store stdout. Cap: 8 `starting`+`running` rows per
`user_sandbox_id`. Insert/reserve **before** provider `start`; the
insert is the cap. Crash: coordinator reaps `starting` with expired
lease (kill if `provider_ref` set).

Lookups for kill: `(org_id, workspace_id, conversation_id, id)` plus
current sandbox id; miss → 404 semantics at the tool (error result,
not 403). Claim: `UPDATE … WHERE id=? AND owner_id IS NOT DISTINCT FROM
? AND owner_until > now()` or `owner_until IS NULL OR owner_until <
now()` when taking over; set a new `owner_id`.

**Tests** (`tests/e2e/` — opens a session)

- Insert / list running by `run_id`.
- CAS claim: expired/unowned row takes a new `owner_id`; stale holder
  cannot update after takeover.

---

## Unit 3 — Coordinator

**Files**

- New `backend/cubeplex/sandbox/command_coordinator.py` (or next to
  `sandbox/cleanup.py`).
- App lifespan — a **dedicated** command-coordinator loop (default ~1s
  tick). Do **not** hang it on `sandbox_cleanup_loop` (60s pause/reap).
- `backend/cubeplex/streams/recovery.py` and stale-run path in
  `run_manager.py` — on stale claim, also claim+kill `lifetime=run`
  rows (phase 2: all rows are run-scoped).

**Interfaces**

- `claim_due_rows(now) -> list[SandboxCommand]` — `owner_until < now`
  or never owned.
- `poll_and_update(row)` — driver `poll` with stored `log_cursor`; on
  exit set status, exit_code, finished_at, `notice_state=pending` if
  `notify_on_complete`; append log file; renew `in_use_until` while
  `running`. Writes CAS on `owner_id`.
- `kill_run(run_id)` — kill provider process + mark `killed` for every
  `running` row with that `run_id`.

**Core logic**

The run worker is not the only poller. After worker death, the
coordinator still polls. Stale recovery CAS-claims `owner_id` +
`owner_until` so a ghost worker cannot inject. Every poll/status/
cursor/notice write is conditional on the same `owner_id`. Pause
reaper stays on `in_use_until` only; coordinator keeps that lease
fresh. Lease duration must be several coordinator ticks (e.g. 15s
lease, 1s poll).

**Tests**

- Unit with LocalSandbox + fake repo: process exits → row `exited`.
- E2E: kill_run leaves no `running` row for that `run_id`.

---

## Unit 4 — `execute` background and `kill_execute`

**Files**

- `backend/cubeplex/middleware/sandbox.py` — args `background`,
  `notify_on_complete`; reject bare `&` / `nohup` / `disown`; HITL
  still in `before_tool_call` before `start`; add `kill_execute`.
- Tool registry / graph assembly if tools are listed explicitly.
- `backend/cubeplex/prompts/sandbox.py` — `background=true` for long
  jobs; no poll/sleep; `notify_on_complete=false` only for servers
  (they still die at run end).

**Interfaces**

`execute`: existing fields + `background: bool = false` +
`notify_on_complete: bool = true` (ignored unless background).

Foreground: reserve row (`starting`, counts toward cap), `start`
(no provider timeout), wait until exit or `timeout_seconds` (default
120, max 1800) by polling, forward `new_output` to `on_update`, `kill`
on CubePlex timeout. If it exits in-wait, mark `exited` and do not
leave a running row.

Background: if `not supports_background()`, tool error (no `cmd &`).
Else reserve row, `start`, set `provider_ref` / `running`, return
immediately. `details`: `{status: "running", command_id, log_path}`.
Text names the id and whether a notice will arrive.

If `start()` fails after reserve, mark the row `killed` so the cap
slot is released.

`kill_execute(command_id: str)` → scoped lookup, `kill`, status
`killed`.

**Core logic**

`cmd &` as the command string → error telling the model to pass
`background=true`. Prompt-cache: one-time prefix change for the new
param + tool. Do not toggle `kill_execute` per turn.

**Tests**

- Unit: `background=true` on a fake non-capable driver errors.
- Unit: `background=true` returns before the fake process exits, with
  `scmd-` in details.
- Unit: command `"sleep 5 &"` errors.
- HITL: confirm still runs `before_tool_call` and deny never `start`s
  (extend existing confirm tests).

---

## Unit 5 — `on_run_end` notify and run-end kill

**Files**

- New middleware or `SandboxMiddleware.on_run_end` —
  CubePlex does not hook `on_run_end` today.
- `backend/cubeplex/streams/run_manager.py` — heartbeat while
  `on_run_end` waits; after `AgentEndEvent` / session return, kill
  leftover rows for the `run_id`; HITL `paused_hitl` still emits
  `DoneEvent(paused=true)` without waiting on notify jobs.
- Cancel / fail paths — same kill as session return.

**Interfaces**

`on_run_end(ctx) -> list[Message] | None`:

- CubePlex wraps `on_run_end` compose (same idea as
  `compose_after_tool_call`): if this run has `running` notify jobs or
  `notice_state=pending` completions, **do not** call other
  `on_run_end` hooks yet (Goal middleware must not treat the run as
  finished). Wait on coordinator row updates (host heartbeat). CAS
  `notice_state` `none`/`pending` → claimed, inject one user message,
  mark `delivered` only after that message is checkpointed. Verify
  Redis still owns this `run_id` immediately before inject.
- If a notify command already `exited`/`killed` with
  `notice_state=pending` when the hook first runs, inject that notice
  (do not skip just because status is not `running`).
- If none remain, run the rest of the `on_run_end` chain and return
  empty from sandbox so CubeLoop emits `AgentEndEvent`.
- HITL suspend: do not wait; return empty and let the existing pause
  path run. Leave `notice_state=pending`; deliver on HITL resume
  (steer into the resumed session). Do not steer into a finished
  session.

Heartbeat: bump `last_event_at` / active-run TTL on an interval while
waiting, even though `_InFlightToolHeartbeat` is zero. Stop when the
run is no longer owned.

**Core logic**

Never “hold CubePlex DoneEvent after `prompt()` returns” to fake another
turn. Completion during HITL pause is `notice_state=pending` on the
row, not injected into a dead loop. Do not block inside cubeloop’s
default concatenated compose, or Goal’s `on_run_end` waits 10 minutes.

**Tests**

- Unit middleware: fake running notify row → `on_run_end` waits then
  returns a user message when the row flips to `exited`.
- Unit/e2e: HITL pause with a running notify row still produces
  `DoneEvent` with `paused=true`.
- E2E or stream test: after a completed run, no `running` row remains
  for that `run_id`.
- Stale path: claiming a stale run kills its command rows.

---

## Unit 6 — Chip overlay and live completion event

**Files**

- Backend: a small SSE event (e.g. `sandbox_command`) with
  `command_id`, `tool_call_id`, `agent_id`, `status`, tail — **not** a
  second CubeLoop `tool_result` for the same call.
- `frontend/packages/core/src/types/events.ts` + `messageStore.ts` —
  apply the event onto `toolResultMap` for the live chip.
- History/bootstrap: overlay `sandbox_commands` by
  `tool_call_id` + `agent_id` when hydrating messages.
- Workspace route (existing conversation/sandbox read path) to load
  running/exited overlay for the current conversation — keep it
  workspace-scoped. No org-admin route.

**Interfaces**

Live: `{type: "sandbox_command", command_id, tool_call_id, agent_id, status, output?}`.
Reload: checkpoint tool result stays `status=running`; overlay from the
table wins for presentation.

**Core logic**

Check only when overlay/live status is `exited` or `killed`. Spinner
while `running`. Subagent cards need `agent_id` on the event.

**Tests** (Vitest)

- Overlay `exited` on a checkpointed `running` result → Check.
- Live `sandbox_command` for a `tool_call_id` updates the chip without
  a second `tool_result` message in history.

---

## Unit 7 — Docs

**Files**

- `docs/site/docs/admin/sandbox.md` / guides sandboxes page — agent can
  start a command and keep going; it is told when the command finishes;
  stopping the run stops leftover commands.

---

## Out of this PR

15s auto-background, `monitor`, `lifetime=conversation`, follow-up
runs, sandbox-panel job list, user Kill button, E2B/Daytona drivers.
