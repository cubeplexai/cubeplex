---
name: debug-cubeplex
description: Use when hitting a bug, test failure, or unexpected behavior in cubeplex — BEFORE proposing or writing a fix. Enforces reproduce-first, then routes to the right cubeplex diagnostic: agent-run traces (cubepi-trace), alembic head conflicts, sandbox/reaper issues, SSE streaming, scope/RBAC 404-vs-403, and check-ci failures. Triggers on phrases like "这个 bug", "测试挂了", "为什么会这样", "报错了", "先别急着改", "run 不对", "迁移冲突", "sandbox 起不来".
---

# Debug cubeplex

Systematic debugging for cubeplex. The discipline is the same everywhere:
**reproduce → locate → understand → fix → verify**. Never propose a fix before
you can reproduce the failure and name the root cause. This skill routes the
symptom to the right cubeplex tool.

## Rule zero: reproduce first

Get a deterministic repro command before touching code — the failing test, a
`curl` against the worktree's `CUBEPLEX_API__PORT` (from `.worktree.env`), or a
recorded run. If you can't reproduce it, you can't confirm you fixed it.

## Route by symptom

**Agent run misbehaved** — missing final reply, a tool did the wrong thing, a
4xx from the model, wrong token/cache numbers, "why did the agent do that?":
→ use **`/cubepi-trace`**. It reads the per-run JSONL span tree
(`cubepi trace ls / view / follow / stats`): errors, tool inputs/outputs,
token usage.

**Migration head conflict after rebase** — a second alembic head appears:
→ do **NOT** `alembic merge heads`. Edit the branch's *first* migration file
to set its `down_revision` to main's new head (keeps history linear). See
[backend/docs/quick-reference.md](../../../backend/docs/quick-reference.md)
→ "Migration head conflicts".

**Sandbox won't start / leftover sandboxes / reaper crash**:
→ check `CUBEPLEX_SANDBOX__*` env, skills PVC (`sandbox.volume.enabled`), and
clean up stragglers with `make backend-cleanup-sandboxes`. Quick-reference
→ "Sandbox Skills Storage".

**Datetime / tz bug** — naive datetime crossing the DB/service boundary:
→ time columns are tz-aware; write `datetime.now(UTC)`, serialize via
`utc_isoformat()`. Quick-reference → "Datetime columns".

**Scope / RBAC surprise** — a cross-org or wrong-scope request:
→ the contract is 404, not 403. Workspace and org-admin routes are separate
handlers; the bug is usually reuse leaking across the scope boundary.

**`make check-ci` fails but the test "should" pass**:
→ first suspect misplaced tests — a test that opens an `AsyncSession` / runs
alembic / hits the app belongs in `backend/tests/e2e/`, not `integration/`.
See `/cubeplex-tdd`.

**SSE / streaming shape wrong**:
→ assert against the event shape in backend e2e; check the agent-run state
machine and drain/replay path.

## Fix, then prove it

Once you know the root cause, make the minimal fix, then **run the original
repro command and paste the output**. Add a regression test that fails without
the fix (see `/cubeplex-tdd`). No "should be fixed now" without evidence.
