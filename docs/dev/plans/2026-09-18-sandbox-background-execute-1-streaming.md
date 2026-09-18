# Plan 1 — Foreground `execute` streaming

**Goal:** While a sandbox command is still running, the chat shows its
output and the chip stays in a running state instead of a silent wait or
an early green check.

**Architecture:** Keep `execute` blocking. Drivers push stdout/stderr
chunks into CubeLoop `on_update`. CubePlex projects
`ToolExecutionUpdateEvent` as SSE `tool_result` updates for the same
`tool_call_id` with `details.status: "running"`. The chip keys off that
status. Truncated output is spilled to a sandbox file. No table, no
`start()`, no `kill_execute`.

**Tech stack:** OpenSandbox `ExecutionHandlers` (driver-private) ·
CubeLoop `on_update` / `ToolExecutionUpdateEvent` · existing SSE
`tool_result` · `ToolCallItem` / `TerminalView` · next-intl.

**Spec:** `docs/dev/specs/2026-09-18-sandbox-background-execute-design.md`
(Phase 1). Follow-ups: plan 2 (managed background), plan 3 (auto-bg /
monitor / conversation lifetime).

**Worktree:** `.worktrees/feat/2026-09-18-sandbox-background-execute`
(slot 81, API 8081, web 3081). Read `.worktree.env` first.

---

## Unit 1 — Driver chunks

**Files**

- `backend/cubeplex/sandbox/base.py` — add optional `on_chunk` to
  `execute` (`Callable[[str], None] | None = None`). Default ignored.
- `backend/cubeplex/sandbox/opensandbox.py` — pass SDK stdout/stderr
  handlers; never leak `ExecutionHandlers` above this file.
- `backend/cubeplex/sandbox/local.py` — read subprocess stdout
  incrementally and call `on_chunk`.
- `backend/cubeplex/sandbox/lazy.py` — forward `on_chunk`; keep
  `_run_with_keepalive` around the whole wait.

**Interfaces**

```
async def execute(
    command, *, timeout=None, envs=None, as_root=False,
    on_chunk: Callable[[str], None] | None = None,
) -> ExecuteResult
```

`on_chunk` receives combined text (stdout+stderr). Timeout still kills
and returns `[timeout]` / exit -1.

**Core logic**

Each chunk is best-effort. A handler exception must not fail the
command. Keepalive already renews `in_use_until` / `last_activity_at`;
if a long wait can still be idle-paused, extend the beat to cover it
(spec success: no idle-pause mid-wait).

**Tests** (`backend/tests/unit/` for LocalSandbox; OpenSandbox handler
wiring can be unit-mocked; anything that opens a session stays e2e)

- LocalSandbox `execute` with a command that prints then sleeps then
  prints: `on_chunk` is called before `execute` returns.
- Timeout still returns `[timeout]` and kills the process.

---

## Unit 2 — Tool `on_update` and truncation spill

**Files**

- `backend/cubeplex/middleware/sandbox.py` — `_make_execute_tool`:
  stop discarding `on_update`; feed driver chunks into it; on truncation
  write full output to a sandbox file and mention the path in the final
  result.

**Interfaces**

`on_update` payload matches existing `AgentToolResult` shape enough for
CubeLoop to emit `ToolExecutionUpdateEvent`: text so far plus
`details={"status": "running"}`. Final `ToolExecutionEndEvent` has
`status` omitted or `"exited"` and the truncated text. Spill path is
absolute inside the sandbox (workdir-relative is fine if `read` can
open it).

**Core logic**

Existing 20k `ToolResultLimitMiddleware` still applies at end. Spill
before that rewrite if the tool itself already truncated. Do not add
`background`. Do not reject `cmd &` here (prompt-only in this PR);
plan 2 owns the hard reject.

**Tests**

- Unit: fake sandbox that calls `on_chunk` twice then returns. The tool
  calls `on_update` at least twice with `status=running`, then a final
  result.
- Unit: oversized output → result mentions a file path; that path was
  uploaded/written on the fake sandbox.

---

## Unit 3 — SSE projection

**Files**

- `backend/cubeplex/agents/stream.py` —
  `_convert_terminal_agent_event`: map `ToolExecutionUpdateEvent` to
  SSE `tool_result` (same `tool_call_id` / `name`).
- `backend/cubeplex/agents/schemas.py` — only if the existing
  `ToolResultEvent` cannot carry `details.status`. Prefer reuse.

**Interfaces**

SSE dict:

```
type: tool_result
tool_call_id, name, result (text so far), details: {status: "running"},
is_error: false
```

Final `ToolExecutionEndEvent` stays the existing `tool_result` mapping.
Unknown events remain dropped.

**Core logic**

Do not invent `tool_result_delta`. Frontend already replaces
`toolResultMap[tcId]` on each `tool_result`. Subagent conversion uses
the same `convert_agent_event_to_sse` — updates must keep `agent_id`
from the envelope the way other tool events do.

**Tests**

- Unit on `convert_agent_event_to_sse`: a `ToolExecutionUpdateEvent`
  yields one `tool_result` with `details.status == "running"`.
- Existing end-event tests still pass.

---

## Unit 4 — Chip honesty

**Files**

- `frontend/packages/web/components/chat/ToolCallItem.tsx` — spinner
  when `details.status === "running"` even if `toolResult` exists.
- `frontend/packages/web/components/chat/ToolCallGroup.tsx` — `isPending`
  must not become false solely because a result object exists.
- `frontend/packages/web/components/panel/TerminalView.tsx` — show
  latest `result` text while running; no fake exit code.
- `frontend/packages/core` types for `toolResultMap` `details` if
  missing.

**Interfaces**

`toolResult.details.status`: `"running"` | absent | `"exited"`. Check
only when status is not `"running"`. Elapsed clock keeps ticking while
running.

**Core logic**

Phase 1 still ends with a real CubeLoop final result, so reload after
the command exits is already durable. No table overlay in this PR.

**Tests** (Vitest)

- Chip with `toolResult.details.status === "running"` shows spinner, not
  Check.
- Chip with a final result (no running status) shows Check.

---

## Unit 5 — Prompt and user docs

**Files**

- `backend/cubeplex/prompts/sandbox.py` — delete the `cmd &` bullet;
  say not to background with `&`; long jobs raise `timeout_seconds`;
  output streams on the chip.
- `docs/site/docs/admin/sandbox.md` and/or
  `docs/site/docs/guides/conversations/sandboxes.md` — one short note
  that command output appears in the chat while the command runs.

**Tests**

- Existing prompt snapshot / string tests if any mention `cmd &`.

---

## Out of this PR

`start` / `poll` / `kill`, `sandbox_commands`, `background=true`,
`kill_execute`, `on_run_end` wait, auto-background, monitor,
conversation lifetime, user Kill list.
