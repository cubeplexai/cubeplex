# Managed sandbox commands

- **Status:** Draft
- **Date:** 2026-09-18
- **Issue:** [#622](https://github.com/cubeplexai/cubeplex/issues/622)
- **Branch / worktree:** `feat/2026-09-18-sandbox-background-execute` / `.worktrees/feat/2026-09-18-sandbox-background-execute`

## Goal

The agent can run a long sandbox command without blocking the turn or
being killed at 120s, see output while it runs, be told when it
finishes, and — for servers and watches — leave that process attached to
the conversation after the run ends. The tool protocol stays
provider-agnostic so OpenSandbox, then E2B / Daytona, implement the same
`Sandbox` methods.

## Context

`execute` waits for the sandbox command to finish. Default timeout is 120
seconds (max 1800). When the cap hits, the process is killed and the tool
returns `[timeout]`. `on_update` is ignored, so the chat shows a silent
chip until the command ends. The sandbox prompt tells the model to use
`cmd &` for backgrounding; that process has no id, no logs, and no owner,
and it disappears when the sandbox pauses or is reaped.

This is the usual path for installs, builds, tests, and anything that
should keep running. The agent cannot do other work in the same turn, and
the user cannot tell a still-running command from a finished one.

OpenSandbox already supports detached commands (status, incremental logs,
interrupt). E2B and Daytona have similar APIs with different handles
(pid vs session+command). CubePlex's `Sandbox` type already hides
provider pause/resume behind `supports_pause()` / `connect_or_resume`;
background must follow that pattern so a later E2B driver does not rewrite
the tool.

CubeLoop already emits `ToolExecutionUpdateEvent` from `on_update`.
CubePlex does not project those events to SSE today.

## Approaches considered

| Approach | Why not / why |
|---|---|
| **A. Raise the timeout** | The agent still blocks. A 20-minute install freezes the turn. Rejected. |
| **B. Keep `cmd &`** | No id, no completion, no kill, pause/reap drops the process. Rejected. |
| **C. Call OpenSandbox background APIs from `execute`** | Leaks command id / log cursor into the tool. E2B and Daytona cannot implement that shape. Rejected. |
| **D. Provider-agnostic `start` / `poll` / `kill` on `Sandbox`, CubePlex-owned command id, one table as the process index** (chosen) | Tool and UI stay stable across providers. Pause reaper keeps using `in_use_until`. The same table covers in-run jobs, auto-background, monitors, and processes that outlive the run. |

**Chosen: D.** Ship in three phases (separate plans / PRs, this spec is
the whole design):

1. Foreground streaming and honest chips — command still blocks the tool
   call, but the user sees output and the process is not a black box.
2. Managed background — `background=true`, in-run completion notice,
   `kill_execute`, table, pause lease. Run end still kills leftovers.
3. Auto-background, Monitor, and conversation-scoped lifetime — a long
   foreground wait becomes a background handle; watches wake on stdout
   lines; servers and monitors can outlive `DoneEvent`.

## Shared design (all phases)

Scope is workspace-only. `execute` stays a workspace agent tool. No
org-admin routes. `provider_ref` (OpenSandbox command id, later an E2B
pid, …) never appears in the tool schema or SSE. The model and UI only
see CubePlex `command_id` values (`scmd-…`).

Do not store stdout in Postgres. Full output lives in a sandbox file
(`log_path`); the model reads it with the existing `read` tool.

Shell `&`, `nohup`, and `disown` are not a supported background
mechanism. If the command string is a bare background operator, the tool
returns an error telling the model to pass `background=true` instead.

HITL `confirm` still runs in `before_tool_call` **before** any
`start()`. Approving a background command starts it; denying never
spawns.

While any row for a sandbox is `running`, renew
`user_sandboxes.in_use_until` (existing lease). The idle-pause reaper
does not need a new SQL join.

Prompt-cache: new parameters and tools change the stable prefix once per
phase that adds them, not per command. Do not toggle tools mid-conversation.

Cap concurrent `starting`+`running` rows per sandbox at 8. In **one
transaction**, lock the `user_sandboxes` row, count those statuses, and
insert the reservation (or fail). A count-then-insert without that lock
is not the cap. Persist `provider_ref` from OpenSandbox `on_init` as
soon as the execution id exists, under the reservation fence — do not
wait for `start()` to return. If `start()` is interrupted with no id,
the coordinator cannot target-kill; do not release the slot until the
sandbox itself is restarted/killed (Unit: lifecycle reconcile).

### Driver contract

On `Sandbox` (`backend/cubeplex/sandbox/base.py`), next to
`supports_pause()`:

- `supports_background() -> bool` — default `False`. Phase 1 may stream
  without this. Phase 2 requires it for `background=true`.
- `start(command, *, timeout=None, envs=None, as_root=False, on_chunk=None) -> ProcessHandle`
  — returns without waiting for exit. `ProcessHandle` has a CubePlex
  `command_id` (assigned by CubePlex, not the driver) and a driver-private
  `provider_ref`.
- `poll(handle) -> ProcessSnapshot` — `running` or `exited` / `killed`,
  optional `exit_code`, and any new output since the last poll
  (OpenSandbox: `get_background_command_logs` + stored `log_cursor`).
- `kill(handle) -> None` — best-effort terminate.

Agent-facing `start()` does **not** pass a provider kill timeout for
commands that may auto-background (phase 3). CubePlex enforces the
foreground deadline by polling and calling `kill`. Bare `sleep` may
still use a provider timeout because it is never auto-promoted.
`on_chunk` is CubePlex-owned. After `start()` returns, OpenSandbox
detached SSE is over; live output comes from `poll().new_output`,
forwarded to `on_chunk` by the wait loop.

OpenSandbox implements this with its detached command API.
`LocalSandbox` implements it with an asyncio subprocess so tests do not
need a real sandbox. A driver with `supports_background() == False` makes
`background=true` (and phase-3 auto-background / Monitor) return a clear
tool error; it does not fall back to `cmd &`.

Infra helpers (`start_browser`, skills sync) may keep using blocking
`execute()`. Agent-facing `execute` moves onto `start` + wait in phase 2
so auto-background in phase 3 is the same wait with a shorter budget.
There is no “promote a blocking execd session” API; wait-then-return-handle
is how auto-background works on every provider.

### Table `sandbox_commands`

New org-scoped business table. Public id prefix `scmd` in
`backend/cubeplex/models/public_id.py`. Created in phase 2; phase 3
adds columns but does not replace the table.

| Column | Phase | Role |
|---|---|---|
| `id` | 2 | `scmd-…` |
| `org_id`, `workspace_id` | 2 | Same scope as the sandbox |
| `user_sandbox_id` | 2 | The `user_sandboxes` row |
| `conversation_id`, `run_id`, `tool_call_id` | 2 | Who started it (`run_id` = starting run) |
| `started_by_user_id` | 2 | Actor for later follow-up `RunContext` |
| `agent_id` | 2 | Main vs subagent, so chip overlay can bind |
| `command`, `description` | 2 | What the model asked |
| `provider`, `provider_ref` | 2 | Driver-private handle; null until `start()` returns |
| `status` | 2 | `starting` / `running` / `exited` / `killed` |
| `notify_on_complete` | 2 | Wake the agent on exit |
| `notice_state` | 2 | `none` / `pending` / `delivered`. Coordinator sets `pending` on exit. Delivered is **not** written from `on_run_end`. |
| `log_path` | 2 | Sandbox file with full output |
| `log_cursor` | 2 | Driver-private poll cursor; not in tool/SSE schema |
| `exit_code`, `finished_at` | 2 | Set when not `running`/`starting` |
| `owner_id` | 2 | Coordinator claim token |
| `owner_until` | 2 | Lease expiry; CAS with `owner_id` |
| `kind` | 3 | `execute` \| `monitor` |
| `lifetime` | 3 | `run` \| `conversation` |
| `notify_run_id` | 3 | Follow-up run started on exit, if any |

Do not store stdout in Postgres. Indexes: `(user_sandbox_id, status)`,
`(run_id, status)`, and in phase 3 `(conversation_id, status)`.

### Chip and terminal panel

Today a tool chip turns into a green check as soon as any `tool_result`
exists. Streaming and background both produce a result before the
process has exited, so that check would lie.

- If `details.status === "running"`, keep the spinner and elapsed time.
  Do not show Check.
- Terminal detail shows `$ command` plus output accumulated so far.
- Live SSE may replace `toolResultMap[tool_call_id]` with later chunks
  (phase 1) or a host completion event keyed by `command_id` /
  `tool_call_id` (phase 2). That live event is **not** a second CubeLoop
  tool-result message. CubeLoop already persisted the start result
  (`status=running`); it does not support pairing a second result onto
  the same call.
- History / refresh hydrates chips by overlaying `sandbox_commands`
  (`tool_call_id` + `agent_id`) onto checkpointed tool results. After
  Redis SSE expiry the table is the source of truth for running vs
  exited, not the original tool-result bytes.

Phase 1 needs the spinner rule for streaming (the blocking call still
ends with one real CubeLoop result). Phases 2–3 add the overlay.

## Phase 1 — Foreground streaming

The tool call still blocks. The user sees output. Timeout still kills.

- Wire `on_update` in `_make_execute_tool`. OpenSandbox (and LocalSandbox)
  deliver stdout/stderr chunks through `on_chunk`.
- Project CubeLoop `ToolExecutionUpdateEvent` to SSE as `tool_result`
  updates for the same `tool_call_id`, with `details.status: "running"`
  and the output so far. The chip stays spinning (shared chip rule).
- Truncate the in-band result (existing 20k tool-result cap still
  applies). When truncated, write the full stream to a sandbox file and
  tell the model that path; it uses `read`.
- Delete the `cmd &` bullet from `backend/cubeplex/prompts/sandbox.py`.
  Do not add `background=true` yet — that lands with the parameter in
  phase 2. After phase 1 the prompt says: do not background with `&`;
  raise `timeout_seconds` for long installs/builds, and expect live
  output in the chip.
- Keep default 120s / max 1800s. Do not treat a longer timeout as the
  fix for long jobs.
- Confirm the existing sandbox lease heartbeat covers the whole blocking
  wait (`in_use_until` / `last_activity_at`). If a long foreground
  execute can still be idle-paused, renew the lease for the wait.

No table, no `start()`, no `kill_execute` in this phase.

## Phase 2 — Managed background (in-run)

The model can start a command and keep working in the same run. The
process is tracked. Completion wakes the same run. The run still owns
the process: when the run ends, leftovers are killed.

### `execute` args (added this phase)

Existing: `description`, `command`, `timeout_seconds`. Add:

- `background: bool = false`
- `notify_on_complete: bool = true` — ignored unless `background` is true

Foreground: `start()`, wait until exit or `timeout_seconds` (default 120,
max 1800), stream via `on_chunk` / `on_update`, kill on timeout. Same
user-visible timeout as today.

Background: `start()`, insert a `sandbox_commands` row, return
immediately. No execute-level kill timeout. `details` includes
`status: "running"`, `command_id`, `log_path`. Return text names the id
and says the model will be notified on exit (or will not, when
`notify_on_complete` is false).

`notify_on_complete` defaults true (builds, tests, installs). The model
sets it false only for processes that are not supposed to exit (dev
servers). Phase 2 still kills those when the run ends.

`kill_execute(command_id)` stops a command started in this conversation's
sandbox. Lookup is `(org_id, workspace_id, conversation_id)` (and the
conversation's current `user_sandbox_id`); mismatch is a scoped 404, not
403. Logs are `read` on `log_path`; there is no logs tool.

Prompt: use `background=true` for long jobs; do not poll or sleep; set
`notify_on_complete=false` only for servers; use `kill_execute` to stop
them. Servers will still die when this run ends (phase 3 changes that).

### Run lifetime (phase 2)

CubeLoop `agent.prompt()` / `session.execute()` returning **ends** the
execution session: the agent is unregistered, the checkpointer scope
closes, and steering has nowhere to go. Withholding CubePlex `DoneEvent`
after that return does **not** keep a turn alive. Do not design around
that.

In-run notify happens **inside** the still-running CubeLoop loop, using
existing `on_run_end` (inject messages and continue, before
`AgentEndEvent`):

1. The model produces a turn with no further tool calls.
2. CubePlex wraps `on_run_end` compose: if this run still has `running`
   notify jobs or `notice_state=pending` completions, skip other
   `on_run_end` hooks (Goal must not see a finished run) and wait on
   the coordinator.
3. Coordinator exit sets `notice_state=pending` (process-owner CAS).
   `on_run_end` injects a user message tagged
   `metadata.notice_id = command_id` if pending and that id is not
   already in the checkpoint. It does **not** mark `delivered` — the
   hook returns before CubeLoop emits/checkpoints the message.
   `run_manager` marks `delivered` on the durable MessageEnd /
   checkpoint path for that `notice_id` (same idea as steering
   `steer_id`). Crash before ack: if history already has `notice_id`,
   mark delivered; else leave pending and inject once more. Notice
   delivery writes do not use the process `owner_id`.
4. When no `running` notify jobs and no `pending` notices remain, run
   the rest of the `on_run_end` chain. CubeLoop emits `AgentEndEvent`.
   **Then** the host kills leftover `lifetime=run` `starting`+`running`
   commands for this `run_id` and emits `DoneEvent`.

While `on_run_end` is waiting, `_InFlightToolHeartbeat` is at zero
(background `execute` already emitted `ToolExecutionEndEvent`). The host
must keep appending run heartbeats (`last_event_at` / TTL) for the whole
wait so a 10-minute build is not marked stale at 180s. Stop that
heartbeat atomically when the run is no longer owned.

**HITL wins.** If CubeLoop suspends (`paused_hitl`), CubePlex **must**
emit `DoneEvent(paused=true)` and detach the worker — a running notify
job does not hold that path. Persist the completion notice on the row;
deliver it on HITL resume (or, in phase 3, as a follow-up run if the
user never resumes). Do not steer into a dead session.

**Coordinator, not “the run worker”. ** A dedicated short-interval
loop (not the 60s pause/reap loop) claims rows with `owner_id` +
`owner_until` (conditional update). It polls provider state via
`poll()` (`new_output` + durable `log_cursor`), renews `in_use_until`,
and records exit. Every status/cursor/notice write CAS-matches
`owner_id`. The run’s `on_run_end` waits on those row updates; it is
not the only poller. If the run worker dies:

- stale recovery claims the run **and** the command rows for that
  `run_id` (CAS on `owner_id` + `owner_until`) so a ghost worker cannot
  still notify;
- phase 2: kill those `lifetime=run` rows (process + status);
- phase 3 `lifetime=conversation` rows stay, claimed by the coordinator.

User Stop and failed run also kill `lifetime=run` rows for that
`run_id`. Refresh during a live run: SSE overlay plus table overlay.
After `DoneEvent` in phase 2 there is nothing to restore — leftovers
were killed. No jobs list and no user Kill control in this phase.

## Phase 3 — Auto-background, Monitor, conversation lifetime

Phase 2 is a prerequisite. This phase does not change the driver
contract. It changes wait policy, adds a watch tool, and lets some rows
outlive `DoneEvent`.

### 3a. Auto-background

Foreground `execute` waits at most **15 seconds** (fixed, not
model-settable). If the process is still running:

- do not kill it;
- treat the call as a background start: same `sandbox_commands` row,
  same `details.status: "running"` return as `background=true`;
- default `notify_on_complete=true` unless the model passed
  `background=true` with `notify_on_complete=false` (explicit background
  still wins).

If it exits inside 15 seconds, the result is a normal foreground
completion (output + exit code), no leftover row.

`timeout_seconds` is the **kill** deadline for a wait that stays
foreground. After auto-background, that kill deadline no longer applies;
lifetime follows the row (`run` or `conversation`).

Do not auto-background a command whose first token is `sleep` (the model
is delaying, not starting work). Explicit `background=true` still works
for `sleep`.

Prompt: if a command is long, either pass `background=true` or let the
15s wait convert it; do not `sleep` to wait for it.

### 3b. Monitor

New tool `monitor`:

- `command` — run in the sandbox via `start()`
- `description` — shown on every wake
- `persistent: bool = false` — if true, no timeout; lives until
  `kill_execute` or the sandbox dies. If false, kill after
  `timeout_seconds` (default 3600, max 36000).
- Each **stdout line** is a wake (inject a notice, agent gets a turn).
  Process exit is also a wake (`DONE` implied by exit).

This is not “wait until my build finishes” — that is `execute` with
`notify_on_complete`. Monitor is for predicates the script can print:
CI went red, a port came up, a job reached a terminal state.

Prompt (required, or the tool will burn turns):

- Print only `DONE`, `FAILED`, or `CANCELLED` (and then exit), or a
  single line when an actionable predicate fires.
- No progress or `CHANGE` lines. Use `grep --line-buffered` in pipes.
- Do not use Monitor to wait on a build; use `execute(background=true)`.

Rate limit, per monitor, so a chatty command cannot wake every line:

- At most one delivered wake every 15 seconds.
- After 3 wakes dropped in a row for rate, or 8 delivered wakes over
  the monitor's life, disable line wakes and keep only the exit wake
  (same as `notify_on_complete`).
- A process that violates the 15s floor continuously for 30 seconds is
  killed.

`kind=monitor` on the row. `kill_execute` stops monitors too.

### 3c. Conversation lifetime

Add `lifetime` on the row:

- `run` (default for phase-2-style jobs and auto-backgrounded builds):
  killed when that `run_id` emits `DoneEvent`, is cancelled, fails, or
  goes stale — same as phase 2.
- `conversation`: survives `DoneEvent`. Still killed on sandbox
  pause/kill, `kill_execute`, or user Kill. Used for
  `notify_on_complete=false` servers and for `monitor` (persistent or
  until its own timeout).

Phase 3 mapping:

| Start | `lifetime` | `notify_on_complete` |
|---|---|---|
| `execute` foreground that exits in 15s | (no row) | — |
| `execute` auto-background or `background=true` with notify true | `run` | true |
| `execute` with `notify_on_complete=false` | `conversation` | false |
| `monitor` | `conversation` | exit still notifies; lines notify until rate-limit promotes to exit-only |

When a `lifetime=run` notify job is still running, `on_run_end` waits as
in phase 2. When only `lifetime=conversation` rows remain, CubeLoop
finishes and the host emits `DoneEvent` **without** killing them.

**Pause/kill of the sandbox** (idle pause fallback, user restart, provider
TTL, `_kill_record`) must reconcile `sandbox_commands`: mark associated
`running` rows `killed`, drop `provider_ref`, and emit an exit wake if
one is still due. Idle pause is normally blocked by `in_use_until`;
manual restart/kill is the path that actually destroys processes.
OpenSandbox pause does not keep live processes; treating pause as kill
for command rows is required.

When a `conversation`-scoped command wakes **after** the starting run
has emitted `DoneEvent`, the coordinator writes a **durable outbox**
row (unique wake id: command + seq or exit). States: `pending` /
`claimed` / `delivered`. Delivery:

- If a compatible run is `running` (not `paused_hitl`), enqueue on the
  existing durable steering queue (one claim).
- If `paused_hitl`, leave `pending` until resume or HITL cancel; do not
  `start_run` on top of the pause lock.
- Otherwise `start_run` with the stored `started_by_user_id` and the
  same conversation (scheduled-task “fixed” destination). Re-check
  org/workspace/conversation membership; 404 if gone. Retry admission
  on the existing one-active-run conflict; do not open a parallel run.

`kill_execute` and user Kill look up `command_id` under
`(org_id, workspace_id, conversation_id, user_sandbox_id)` and return
the normal scoped 404 on mismatch.

Stale-run recovery kills only `lifetime=run` rows for that `run_id`.
It must not kill `conversation` rows.

### 3d. UI for conversation-scoped commands

Phase 2 chip rules still apply on the spawn bubble.

Additionally:

- Sandbox side panel (existing Terminal tab, or a thin list above it)
  shows `running` rows for this conversation's sandbox: description,
  elapsed, Kill. Refresh loads from `sandbox_commands`, not from the
  dead run's SSE.
- User Kill calls the same path as `kill_execute` (workspace-scoped,
  membership required). No org-admin route.
- A completed follow-up run renders as a normal turn (injected notice +
  model reply). Do not invent a second inbox.

## Delivery

Three implementation plans / PRs after this spec. Do not merge them
into one PR.

| Plan / PR | Phase | What ships |
|---|---|---|
| 1 | Foreground streaming | `on_chunk` / SSE updates, truncate-to-file, chip honesty for `status=running`, drop `cmd &` from the prompt, lease for the blocking wait |
| 2 | Managed background | Driver `start`/`poll`/`kill`, table, coordinator lease, `background` + `kill_execute`, `on_run_end` notify, run-end kill, prompt for background |
| 3 | Auto-bg + Monitor + conversation lifetime | 15s wait, `monitor` tool + rate limits, `lifetime` column, follow-up run, panel list + user Kill |

User-facing docs (`docs/site`) update in the same PR as the behavior
they describe (streaming chip, then background, then monitor / servers
that survive the turn).

## Out of scope

- E2B / Daytona driver implementations (the ABC must not block them;
  they are separate work).
- Shell-level `&` as a supported background mechanism.
- Storing full command output in Postgres.
- Changing the default foreground **kill** timeout (120s) or max (1800s).
  Phase 3 only adds a 15s **block** budget before auto-background.
- Auto-background of `sleep`.
- Cross-conversation process move, or processes that survive sandbox
  pause/kill.
- A generic job runtime for non-shell tools (image gen, subagents).

## Success criteria

### Phase 1

- A foreground `execute` that prints over several seconds shows that
  output in the terminal panel before the command exits.
- The execute chip stays in a running state during that stream; it does
  not show a green check on the first chunk.
- Truncated output points at a sandbox file the model can `read`.
- The prompt no longer recommends `cmd &`.
- A long foreground execute does not get idle-paused mid-wait.

### Phase 2

- `execute(..., background=true)` returns in well under the command's
  runtime, with a `scmd-` id. The agent continues with other tools in
  the same turn.
- A background test/build with default `notify_on_complete` finishes
  **before** CubeLoop `AgentEndEvent`: `on_run_end` injects the exit
  notice, the model takes another turn, then `DoneEvent` follows as
  today. Withholding `DoneEvent` after `prompt()` returns is not used.
- A 10-minute notify wait does not get stale-reaped at 180s (host
  heartbeat stays up). HITL pause still emits `DoneEvent(paused=true)`
  even if a notify command is running.
- The execute chip stays running until that exit.
- `kill_execute` stops the process; the row becomes `killed`.
- User Stop / stale run leaves no `running` row for that `run_id`.
- A sandbox with a `running` command is not idle-paused.
- `cmd &` in the command string is rejected with a message to use
  `background=true`.
- `background=true` on a driver with `supports_background() == False`
  errors; it does not spawn `cmd &`.
- Tool schema and SSE payloads never contain OpenSandbox command ids,
  E2B pids, or log cursors.
- HITL `confirm` still gates a background `execute` before `start()`.

### Phase 3

- A foreground command still running after 15s returns a `scmd-` id and
  the process keeps running; the chip stays in the running state. The
  same command finishing in 2s is a normal foreground result with no
  leftover row.
- `sleep 30` without `background=true` is not auto-backgrounded; it
  waits as a foreground command (and still dies at the kill timeout).
- `execute(..., notify_on_complete=false)` for a dev server survives
  `DoneEvent`. Refresh shows it in the sandbox panel. User Kill or
  `kill_execute` stops it. Idle-pause does not run while it is
  `running`.
- A `monitor` script that prints `FAILED` once wakes the agent with that
  line. A script that prints a progress line every second does not get
  a turn per line; after the rate-limit promotion it only wakes on exit
  (or is killed if it keeps flooding for 30s).
- A monitor or server firing after the starting run has ended starts at
  most one follow-up run on that conversation (or steers the already
  active run). It does not open a parallel run on the same conversation.
- Stale recovery of the old run does not kill the conversation-scoped
  server.
