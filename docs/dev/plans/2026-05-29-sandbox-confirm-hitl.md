# Sandbox `confirm` → real HITL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a saved sandbox `confirm` command-rule actually pause the `execute` tool and run/skip it based on a human approve/deny, instead of degrading to deny.

**Architecture:** Bump cubepi to the HITL-bearing pin (+ its v1→v2 checkpointer migration), move the command-policy gate out of the `execute` tool body into a `before_tool_call` hook on `SandboxMiddleware` backed by a per-run `InMemoryChannel`, carry the human's answer back to the still-blocked worker over the existing Redis control channel, and surface the pending/resolved states to the chat UI via two new SSE events + an inline confirm card.

**Tech Stack:** Python 3.12 / FastAPI / cubepi (pinned git dep) / Alembic / Redis pub-sub / Postgres / Next.js + React 19 / pnpm.

**Spec:** `docs/dev/specs/2026-05-29-sandbox-confirm-hitl-design.md`

---

## Pre-flight (read before Task 1)

- **Worktree:** `/home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl`, branch
  `feat/sandbox-confirm-hitl`. First command in any session:
  `cat .worktree.env` (ports: backend **8090**, frontend **3090**; DB
  `cubeplex_feat_sandbox_confirm_hitl`). Never assume 8000/3000.
- **Run backend commands from** `.../feat/sandbox-confirm-hitl/backend` with
  `uv run ...`. Tests auto-route to the per-slot test DB (conftest); plain
  `uv run pytest` is safe.
- **Stay on the feature branch.** No switching to main, no merges mid-execution.
- **cubepi HITL surface** (already in the pinned-after-bump version, import from
  `cubepi.hitl`): `InMemoryChannel`, `ApproveAnswer`, `HitlTimedOut`,
  `HitlCancelled`, and the `HitlChannel` protocol type. `BeforeToolCallResult`
  is from `cubepi.agent.types`. Event classes `HitlRequestEvent` /
  `HitlAnswerEvent` are from `cubepi.agent.types`.
- **Two PRs** (split decided in spec §8): Tasks 1–7 = backend PR
  ("backend: confirm pauses & resumes"); Tasks 8–12 = frontend PR
  ("frontend: answer a confirm"). Backend merges first; the SSE/endpoint
  contract is frozen in this plan so the frontend can be built in parallel.

---

## Task 1: Bump cubepi pin + cubepi v1→v2 checkpointer migration

**Files:**
- Modify: `backend/pyproject.toml` (cubepi git `rev`)
- Modify: `backend/uv.lock` (via `uv lock`, do not hand-edit)
- Create: `backend/alembic/versions/<rev>_cubepi_v1_to_v2_pending_request.py`

**Why first:** the pin bump raises cubepi `EXPECTED_SCHEMA_VERSION` 1→2;
`cubepi.PostgresCheckpointer` refuses to start on a v1 DB, so the migration
must land in the same commit. The migration is **autogenerate + one hand-added
line** (spec §4.1): the new `pending_request` column sits on `cubepi_threads`
(a regular table that `env.py` does NOT exclude), so autogen emits the
`ADD COLUMN`; only the `cubepi_schema_version` data-row bump is hand-added.

- [ ] **Step 1: Find the target cubepi rev**

Run:
```bash
cd /home/chris/cubepi && git log --oneline -5
```
Expected: a list of cubepi `main` commits whose messages include the HITL work
(e.g. `refactor(hitl): rename _UNSET sentinel ...`, `fix(hitl): ...`). Pick the
newest `main` commit SHA (call it `<CUBEPI_REV>`).

- [ ] **Step 2: Update the pin**

In `backend/pyproject.toml`, find the `[tool.uv.sources]` line:
```toml
cubepi = { git = "https://github.com/cubeplexai/cubepi.git", rev = "9cb6817" }
```
Replace `9cb6817` with `<CUBEPI_REV>`. Also update the adjacent comment block
that documents what the pin points at, to say it includes the HITL channel.

- [ ] **Step 3: Re-lock**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv lock --upgrade-package cubepi
```
Expected: `uv.lock` updates the cubepi `git ...?rev=<CUBEPI_REV>` entry; exit 0.

- [ ] **Step 4: Sync the env**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv sync
```
Expected: cubepi reinstalled at the new rev; exit 0.

- [ ] **Step 5: Confirm cubepi now reports schema v2 and exposes HITL**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run python -c "from cubepi.checkpointer.postgres.models import EXPECTED_SCHEMA_VERSION; from cubepi.hitl import InMemoryChannel, ApproveAnswer, HitlTimedOut, HitlCancelled; from cubepi.agent.types import HitlRequestEvent, HitlAnswerEvent, BeforeToolCallResult; print('schema', EXPECTED_SCHEMA_VERSION)"
```
Expected: prints `schema 2` and no ImportError. If schema is still 1, the rev
chosen does not include the HITL work — go back to Step 1.

- [ ] **Step 6: Generate the migration via autogenerate**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run alembic revision --autogenerate -m "cubepi v1 to v2 pending_request"
```
Expected: a new file under `backend/alembic/versions/` whose `upgrade()`
contains `op.add_column('cubepi_threads', sa.Column('pending_request', ...JSONB...))`
and whose `downgrade()` contains `op.drop_column('cubepi_threads', 'pending_request')`.

- [ ] **Step 7: Inspect the generated diff — confirm it ONLY adds the column**

Open the generated file. Verify `upgrade()` contains exactly the
`add_column('cubepi_threads', ...)` op and **nothing else** (no drops of other
tables, no unrelated index changes). If autogen captured extra noise (e.g. it
tried to touch `cubepi_messages` partitions or other tables), delete those ops
— they belong to excluded cubepi tables and must not be in a host migration.

- [ ] **Step 8: Hand-add the schema-version bump (the one line autogen can't produce)**

Edit the generated file. At the END of `upgrade()`, append:
```python
    from cubepi.checkpointer.postgres.alembic_helpers import write_schema_version_op

    op.execute(write_schema_version_op())
```
At the END of `downgrade()`, append:
```python
    op.execute(
        "DELETE FROM cubepi_schema_version WHERE version <> 1; "
        "INSERT INTO cubepi_schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;"
    )
```

- [ ] **Step 9: Confirm the migration chains onto the current head**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run alembic heads
```
Expected: a single head (the new revision). If there are two heads, set the new
file's `down_revision` to `28c4c57516f6` and re-run until there is exactly one.

- [ ] **Step 10: Apply it**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run alembic upgrade head
```
Expected: applies cleanly; exit 0.

- [ ] **Step 11: Verify the column and version landed**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run python -c "
import asyncio, asyncpg
from cubeplex.config import config as c
async def main():
    conn = await asyncpg.connect(host=c.get('database.host','localhost'), port=c.get('database.port',5432), user=c.get('database.user','postgres'), password=c.get('database.password',''), database=c.get('database.name'))
    col = await conn.fetchval(\"SELECT data_type FROM information_schema.columns WHERE table_name='cubepi_threads' AND column_name='pending_request'\")
    ver = await conn.fetchval('SELECT version FROM cubepi_schema_version LIMIT 1')
    print('col', col, 'ver', ver)
    await conn.close()
asyncio.run(main())
"
```
Expected: `col jsonb ver 2`.

- [ ] **Step 12: Round-trip downgrade/upgrade once (safety)**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: both succeed; exit 0. (Confirms downgrade SQL is valid.)

- [ ] **Step 13: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/pyproject.toml backend/uv.lock backend/alembic/versions/ && git commit -m "feat(sandbox): bump cubepi to HITL pin + cubepi v1->v2 migration"
```

---

## Task 2: Move the policy gate into `SandboxMiddleware.before_tool_call`

**Files:**
- Modify: `backend/cubeplex/middleware/sandbox.py`
- Test: `backend/tests/unit/test_sandbox_confirm_gate.py` (create)

The `execute` tool body currently evaluates rules and degrades `confirm` to
deny (sandbox.py ~lines 144–188). Remove that; add a `before_tool_call` hook on
the middleware class. `evaluate_command` stays the matcher (unchanged).

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_sandbox_confirm_gate.py`:
```python
"""SandboxMiddleware.before_tool_call command-policy gate."""
from __future__ import annotations

import pytest
from cubepi.hitl import ApproveAnswer, HitlCancelled, HitlTimedOut

from cubeplex.middleware.sandbox import SandboxMiddleware


class _ToolCall:
    def __init__(self, name: str, id: str = "call_1") -> None:
        self.name = name
        self.id = id


class _Args:
    def __init__(self, command: str) -> None:
        self.command = command


class _Ctx:
    def __init__(self, name: str, command: str) -> None:
        self.tool_call = _ToolCall(name)
        self.args = _Args(command)


class _StubChannel:
    """Records the approve() call and returns a scripted answer/raises."""

    def __init__(self, *, answer=None, raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.calls: list[dict] = []

    async def approve(self, *, tool_name, tool_call_id, args, details, timeout, signal=None):
        self.calls.append(
            {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": args,
             "details": details, "timeout": timeout}
        )
        if self._raises is not None:
            raise self._raises
        return self._answer


class _FakeSandbox:
    workdir = "/work"


def _mw(channel, rules):
    return SandboxMiddleware(
        sandbox=_FakeSandbox(), conversation_id="c1", workspace_id="w1",
        command_rules=rules, channel=channel,
    )


@pytest.mark.asyncio
async def test_non_execute_tool_is_ignored():
    ch = _StubChannel()
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("write_file", "rm -rf /"), signal=None)
    assert res is None
    assert ch.calls == []


@pytest.mark.asyncio
async def test_allow_passes_without_channel_call():
    ch = _StubChannel()
    mw = _mw(ch, [{"action": "deny", "pattern": "shutdown"}])
    res = await mw.before_tool_call(_Ctx("execute", "ls -la"), signal=None)
    assert res is None
    assert ch.calls == []


@pytest.mark.asyncio
async def test_deny_blocks_without_channel_call():
    ch = _StubChannel()
    mw = _mw(ch, [{"action": "deny", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is not None and res.block is True
    assert ch.calls == []
    assert res.hitl_trace["decision"] == "policy_deny"


@pytest.mark.asyncio
async def test_confirm_approve_runs_tool():
    ch = _StubChannel(answer=ApproveAnswer(decision="approve"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is None  # not blocked → tool runs
    assert ch.calls[0]["tool_name"] == "execute"
    assert ch.calls[0]["args"] == {"command": "rm -rf /tmp/x"}
    assert ch.calls[0]["details"]["matched_pattern"] == "rm *"
    assert ch.calls[0]["timeout"] == 180.0


@pytest.mark.asyncio
async def test_confirm_deny_blocks():
    ch = _StubChannel(answer=ApproveAnswer(decision="deny", reason="nope"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "human_deny"
    assert res.hitl_trace["reason"] == "nope"


@pytest.mark.asyncio
async def test_confirm_timeout_blocks_as_deny():
    ch = _StubChannel(raises=HitlTimedOut(180.0))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "timed_out"
    assert res.deny_reason == "approval_timeout"


@pytest.mark.asyncio
async def test_confirm_cancel_blocks():
    ch = _StubChannel(raises=HitlCancelled("user closed"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "cancelled"


@pytest.mark.asyncio
async def test_confirm_edit_is_rejected():
    ch = _StubChannel(answer=ApproveAnswer(decision="edit", edited_args={"command": "ls"}))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    with pytest.raises(ValueError):
        await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)


@pytest.mark.asyncio
async def test_no_channel_means_no_gate():
    mw = _mw(None, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_sandbox_confirm_gate.py -q
```
Expected: FAIL — `SandboxMiddleware.__init__` rejects `channel=` (unexpected
kwarg) and/or `before_tool_call` does not exist.

- [ ] **Step 3: Strip the gate out of the execute tool body**

In `backend/cubeplex/middleware/sandbox.py`, in `_make_execute_tool`, delete the
`command_rules` parameter usage and the entire `action, pattern = evaluate_command(...)`
+ `if action == "deny"` + `if action == "confirm"` block inside `_execute`.
After the edit, `_execute` starts directly at:
```python
    async def _execute(
        tool_call_id: str,
        args: _ExecuteArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        result = await sandbox.execute(args.command)
        if workspace_id is not None and conversation_id is not None and result.exit_code == 0:
            _record_executed(workspace_id, conversation_id, args.command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return AgentToolResult(content=[TextContent(text=output)])
```
Also remove the now-unused `command_rules` parameter from `_make_execute_tool`'s
signature and its call site in `SandboxMiddleware.__init__` (the execute tool no
longer needs rules). Keep `workspace_id` / `conversation_id`.

- [ ] **Step 4: Add the imports the hook needs**

At the top of `sandbox.py`, alongside the existing
`from cubeplex.sandbox_policy.rules import evaluate_command`, add:
```python
from cubepi.agent.types import BeforeToolCallResult
from cubepi.hitl import ApproveAnswer, HitlCancelled, HitlChannel, HitlTimedOut
```
(`ApproveAnswer` is used by tests/type-clarity; `HitlChannel` is the type for
the new constructor arg.)

- [ ] **Step 5: Accept `channel` in the constructor**

In `SandboxMiddleware.__init__`, add the parameter and store it:
```python
    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        command_rules: list[dict[str, Any]] | None = None,
        channel: HitlChannel | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.workspace_id = workspace_id
        self.command_rules = command_rules or []
        self.channel = channel
```
Keep building `self._tools` as before, but drop `command_rules=` from the
`_make_execute_tool(...)` call (per Step 3).

- [ ] **Step 6: Implement `before_tool_call`**

Add this method to `SandboxMiddleware` (e.g. just above `transform_system_prompt`):
```python
    async def before_tool_call(
        self,
        ctx: Any,
        *,
        signal: object = None,
    ) -> BeforeToolCallResult | None:
        """Enforce command rules before the execute tool runs.

        v1: execute only. deny → block; confirm → pause on the HITL channel
        (approve runs it, deny/timeout/cancel block it); edit is rejected.
        """
        if getattr(ctx.tool_call, "name", None) != "execute":
            return None
        if self.channel is None or not self.command_rules:
            return None

        command = ctx.args.command
        action, pattern = evaluate_command(command, self.command_rules)
        if action == "allow":
            return None
        if action == "deny":
            return BeforeToolCallResult(
                block=True,
                reason=f"command blocked by org policy: {pattern}",
                deny_reason=pattern,
                hitl_trace={"decision": "policy_deny", "pattern": pattern},
            )

        # action == "confirm": pause and ask the human.
        try:
            answer = await self.channel.approve(
                tool_name="execute",
                tool_call_id=ctx.tool_call.id,
                args={"command": command},
                details={"matched_pattern": pattern, "command": command},
                timeout=180.0,
                signal=signal,
            )
        except HitlTimedOut:
            return BeforeToolCallResult(
                block=True,
                reason="approval timed out (180s); command not run",
                deny_reason="approval_timeout",
                hitl_trace={"decision": "timed_out"},
            )
        except HitlCancelled as exc:
            return BeforeToolCallResult(
                block=True,
                reason=f"cancelled: {exc.reason}",
                deny_reason=f"cancelled: {exc.reason}",
                hitl_trace={"decision": "cancelled", "reason": exc.reason},
            )

        if answer.decision == "approve":
            return None
        if answer.decision == "deny":
            return BeforeToolCallResult(
                block=True,
                reason=answer.reason or "denied by user",
                deny_reason=answer.reason or "denied by user",
                hitl_trace={"decision": "human_deny", "reason": answer.reason},
            )
        raise ValueError("edit decision not supported for sandbox confirm v1")
```

- [ ] **Step 7: Run the unit tests to green**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_sandbox_confirm_gate.py -q
```
Expected: all PASS.

- [ ] **Step 8: Run the existing sandbox unit tests + type check**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit -k sandbox -q && uv run mypy cubeplex/middleware/sandbox.py
```
Expected: PASS / `Success: no issues found`. If an existing unit test asserted
the old "confirm degrades to deny in the tool body", update it to drive the new
`before_tool_call` path (or delete if now covered by the new test file).

- [ ] **Step 9: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/cubeplex/middleware/sandbox.py backend/tests/unit/test_sandbox_confirm_gate.py && git commit -m "feat(sandbox): move command-policy gate to before_tool_call HITL hook"
```

---

## Task 3: Wire a per-run channel in `run_manager` + agent factory

**Files:**
- Modify: `backend/cubeplex/agents/graph.py` (add `channel` kwarg)
- Modify: `backend/cubeplex/streams/run_manager.py` (create channel, pass it, register/teardown)

**Note on `run_manager.py`:** locate insertion points by the named landmarks
below (not absolute line numbers).

- [ ] **Step 1: Add `channel` to the agent factory**

In `backend/cubeplex/agents/graph.py`, add the import and parameter:
```python
from cubepi.hitl import HitlChannel
```
Add `channel: HitlChannel | None = None,` to `create_cubeplex_agent`'s keyword
args (next to `thinking`), and pass `channel=channel` into the `Agent(...)`
constructor call (alongside `checkpointer=checkpointer`).

- [ ] **Step 2: Register a per-run channel dict in RunManager.__init__**

In `run_manager.py`, find the line `self._agents: dict[str, Any] = {}` in
`__init__` and add directly below it:
```python
        self._hitl_channels: dict[str, Any] = {}
```

- [ ] **Step 3: Create the channel where the sandbox middleware is built**

In `_run_cubepi_path`, find the `SandboxMiddleware(` construction (inside the
`if sandbox is not None:` block). Immediately before it, create the channel:
```python
                from cubepi.hitl import InMemoryChannel

                sandbox_hitl_channel = InMemoryChannel(default_timeout=180.0)
```
Then pass `channel=sandbox_hitl_channel` into the `SandboxMiddleware(...)` call.
If the sandbox block is skipped (no sandbox), leave `sandbox_hitl_channel = None`
defined before the block so later references are safe:
```python
        sandbox_hitl_channel = None
```
(Place this default just before `if sandbox is not None:`.)

- [ ] **Step 4: Pass the channel into the agent factory**

Find the `create_cubeplex_agent(` call in `_run_cubepi_path` and add
`channel=sandbox_hitl_channel,` to its kwargs.

- [ ] **Step 5: Register the channel next to the agent**

Find `self._agents[run_id] = agent` and add directly below it:
```python
            if sandbox_hitl_channel is not None:
                self._hitl_channels[run_id] = sandbox_hitl_channel
```

- [ ] **Step 6: Tear it down everywhere the agent is torn down**

There are **two** `self._agents.pop(run_id, None)` sites in `run_manager.py`
(around lines 1575 and 2064). Add directly below **each** of them:
```python
                self._hitl_channels.pop(run_id, None)
```
Match the surrounding indentation at each site.

- [ ] **Step 7: Type check + import smoke**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run mypy cubeplex/agents/graph.py cubeplex/streams/run_manager.py && uv run python -c "import cubeplex.streams.run_manager, cubeplex.agents.graph; print('ok')"
```
Expected: `Success: no issues found` and `ok`.

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/cubeplex/agents/graph.py backend/cubeplex/streams/run_manager.py && git commit -m "feat(sandbox): per-run InMemoryChannel wired into agent + sandbox middleware"
```

---

## Task 4: Accept the human answer (in-process fast path + cross-worker control)

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`
- Test: `backend/tests/unit/test_run_manager_hitl_answer.py` (create)

**Real code shape (confirmed):** `_handle_control(self, data: dict[str, Any])`
receives an **already-parsed dict** (the `_subscribe_loop` does `json.loads`);
it dispatches on `type_ = data.get("type")` with `run_id = data.get("run_id")`
and branches `if type_ == "cancel" / elif "steer" / elif "cancel_steer"` (no
final `else`). Cross-worker publishing goes through the
`_publish_control(run_id, type_, content=..., steer_id=...)` helper. The HTTP
layer calls public `dispatch_*` methods (e.g. `dispatch_steer`) that try the
**in-process** agent first and fall back to `_publish_control` for other
workers. We mirror that exactly: same-worker answers hit the channel directly;
cross-worker answers round-trip through the control channel.

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_run_manager_hitl_answer.py`:
```python
"""RunManager delivers a HITL answer to the run's in-process channel and
routes a cross-worker hitl_answer control message."""
from __future__ import annotations

import pytest
from cubepi.hitl import ApproveAnswer


class _RecordingChannel:
    def __init__(self) -> None:
        self.answers: list[tuple[str, ApproveAnswer]] = []

    async def answer(self, question_id: str, answer: ApproveAnswer) -> None:
        self.answers.append((question_id, answer))


@pytest.mark.asyncio
async def test_dispatch_hitl_answer_delivers_in_process(run_manager_factory):
    rm = run_manager_factory()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    result = await rm.dispatch_hitl_answer("run_1", "call_9", "approve", None)
    assert result == "delivered"
    assert ch.answers == [("call_9", ApproveAnswer(decision="approve", reason=None))]


@pytest.mark.asyncio
async def test_dispatch_hitl_answer_publishes_when_not_local(run_manager_factory):
    rm = run_manager_factory()  # fake redis records publish() calls
    result = await rm.dispatch_hitl_answer("ghost", "call_9", "deny", "no")
    assert result == "published"


@pytest.mark.asyncio
async def test_handle_control_routes_hitl_answer(run_manager_factory):
    rm = run_manager_factory()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    await rm._handle_control(
        {"type": "hitl_answer", "run_id": "run_1",
         "tool_call_id": "call_9", "decision": "approve", "reason": None}
    )
    assert ch.answers == [("call_9", ApproveAnswer(decision="approve", reason=None))]


@pytest.mark.asyncio
async def test_handle_control_hitl_answer_unknown_run_is_dropped(run_manager_factory):
    rm = run_manager_factory()
    await rm._handle_control(
        {"type": "hitl_answer", "run_id": "ghost",
         "tool_call_id": "call_9", "decision": "deny", "reason": "x"}
    )  # must not raise
```

If there is no existing `run_manager_factory` fixture, add one to
`backend/tests/unit/conftest.py` (or the nearest unit conftest). Construct a
`RunManager(app=<stub>, redis=<fake>, key_prefix="t", run_event_ttl_seconds=60)`
(see `RunManager.__init__` at run_manager.py:461). The fake redis needs an async
`publish(self, channel, data)` that records calls and returns 0. Copy any
existing run_manager unit test's construction if one exists.

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_run_manager_hitl_answer.py -q
```
Expected: FAIL — `dispatch_hitl_answer` does not exist and `_handle_control` has
no `hitl_answer` branch.

- [ ] **Step 3: Extend `_publish_control` to carry HITL fields**

The current helper (run_manager.py ~588) only forwards `content` / `steer_id`.
Add an optional `extra` dict merged into the payload. Replace the helper with:
```python
    async def _publish_control(
        self,
        run_id: str,
        type_: str,
        content: str | None = None,
        steer_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        import json

        payload: dict[str, Any] = {"run_id": run_id, "type": type_}
        if content is not None:
            payload["content"] = content
        if steer_id is not None:
            payload["steer_id"] = steer_id
        if extra:
            payload.update(extra)
        await self._redis.publish(self._control_channel, json.dumps(payload))
```

- [ ] **Step 4: Add the public `dispatch_hitl_answer` (mirrors `dispatch_steer`)**

Find `dispatch_steer` (run_manager.py ~609) and add directly below it:
```python
    async def dispatch_hitl_answer(
        self, run_id: str, tool_call_id: str, decision: str, reason: str | None = None
    ) -> str:
        """Deliver a human approve/deny for a pending sandbox confirm.

        In-process fast path when this worker holds the run's channel; otherwise
        publish on the control channel so the worker that does can deliver it.
        """
        if await self._deliver_hitl_answer(run_id, tool_call_id, decision, reason):
            return "delivered"
        await self._publish_control(
            run_id,
            "hitl_answer",
            extra={"tool_call_id": tool_call_id, "decision": decision, "reason": reason},
        )
        return "published"

    async def _deliver_hitl_answer(
        self, run_id: str, tool_call_id: str, decision: str, reason: str | None
    ) -> bool:
        """Answer the in-process channel if present. Returns True if delivered."""
        from cubepi.hitl import ApproveAnswer

        channel = self._hitl_channels.get(run_id)
        if channel is None:
            return False
        if decision not in ("approve", "deny"):
            return False
        try:
            await channel.answer(tool_call_id, ApproveAnswer(decision=decision, reason=reason))
        except Exception:
            logger.warning("hitl_answer delivery failed for run {}", run_id, exc_info=True)
        return True
```

- [ ] **Step 5: Add the `hitl_answer` branch to `_handle_control`**

Find the `elif type_ == "cancel_steer":` branch (run_manager.py ~672) and add a
new branch after it:
```python
        elif type_ == "hitl_answer":
            await self._deliver_hitl_answer(
                run_id,
                data.get("tool_call_id") or "",
                data.get("decision") or "",
                data.get("reason"),
            )
```

- [ ] **Step 6: Run the tests to green**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_run_manager_hitl_answer.py -q && uv run mypy cubeplex/streams/run_manager.py
```
Expected: PASS / `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/cubeplex/streams/run_manager.py backend/tests/unit/ && git commit -m "feat(sandbox): deliver hitl answers in-process + cross-worker control"
```

---

## Task 5: HTTP endpoint to submit a confirm answer

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Test: covered by the Task 7 E2E (endpoint is thin glue over Task 4)

The endpoint is workspace-scoped and mirrors the existing cancel/steer handler
in the same file (auth dependencies + `RunManager` access). Active-run
resolution uses the same `get_active_run(...)` helper run_manager imports
(`cubeplex.streams.run_manager:20`).

- [ ] **Step 1: Read the existing cancel/steer endpoint as the template**

Open `backend/cubeplex/api/routes/v1/conversations.py` and locate the handler(s)
that call `RunManager.dispatch_cancel` / `dispatch_steer`. Note verbatim: the
route decorator + method, the auth/dependency params (workspace membership,
current user, the `RunManager` accessor dependency), and how it obtains the
`run_id` for `{conversation_id}` (it resolves the active run — find whether it
uses `get_active_run(...)` directly or a thin wrapper). The new handler copies
that scaffolding.

- [ ] **Step 2: Add the request model**

Near the other Pydantic request models in this file (match the file's
convention for where models live), add:
```python
class SandboxConfirmAnswer(BaseModel):
    decision: Literal["approve", "deny"]
    reason: str | None = None
```
Ensure `from typing import Literal` and `from pydantic import BaseModel` are
imported (add if missing).

- [ ] **Step 3: Add the endpoint**

Add a handler with the SAME decorator style + dependency params the cancel
handler uses. Resolve the active run exactly as the cancel handler does, then
delegate to Task 4's `dispatch_hitl_answer`:
```python
@router.post("/{conversation_id}/sandbox-confirm/{tool_call_id}")
async def submit_sandbox_confirm(
    workspace_id: str,
    conversation_id: str,
    tool_call_id: str,
    body: SandboxConfirmAnswer,
    # --- copy the SAME dependency params the cancel handler uses ---
    # (current user / workspace membership guard / RunManager accessor)
) -> dict[str, str]:
    run_id = await get_active_run(...)  # resolve EXACTLY as the cancel handler
                                        # does (same args: redis/key_prefix +
                                        # conversation_id). Import get_active_run
                                        # from cubeplex.streams.<active-run module>
                                        # — the same module run_manager imports
                                        # it from (run_manager.py:20).
    if not run_id:
        raise HTTPException(status_code=404, detail="no active run for conversation")
    await run_manager.dispatch_hitl_answer(
        run_id=run_id,
        tool_call_id=tool_call_id,
        decision=body.decision,
        reason=body.reason,
    )
    return {"status": "submitted"}
```
Replace the `...` / comment lines with the cancel handler's actual deps and
active-run resolution. Keep the path workspace-scoped under the conversations
router prefix; do NOT add a `?scope=` switch (scope-isolation rule).

- [ ] **Step 4: Type check + route registration smoke**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run mypy cubeplex/api/routes/v1/conversations.py && uv run python -c "
from cubeplex.api.app import build_app  # use the real factory; grep app/__init__ or main.py if unsure
app = build_app()
paths = [getattr(r, 'path', '') for r in app.routes]
assert any('sandbox-confirm' in p for p in paths), [p for p in paths if 'conversations' in p]
print('route registered')
"
```
Expected: `Success: no issues found` and `route registered`. (Confirm the app
factory's real name from `cubeplex/api/app.py`; adjust the import if needed.)

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/cubeplex/api/routes/v1/conversations.py && git commit -m "feat(sandbox): POST sandbox-confirm endpoint for HITL answers"
```

---

## Task 6: Translate HITL events into SSE

**Files:**
- Modify: `backend/cubeplex/agents/stream.py`
- Test: `backend/tests/unit/test_stream_hitl_events.py` (create)

`convert_agent_event_to_sse(evt)` returns `list[dict]` and drops unknown event
types. Add two cases, gated to approve-kind HITL on the `execute` tool.

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_stream_hitl_events.py`:
```python
"""HITL AgentEvents → cubeplex SSE dicts."""
from __future__ import annotations

from cubepi.agent.types import HitlAnswerEvent, HitlRequestEvent
from cubepi.hitl import ApproveAnswer
from cubepi.hitl.types import ApproveRequest, HitlRequest

from cubeplex.agents.stream import convert_agent_event_to_sse


def _approve_request(tool_call_id="call_1", command="rm -rf /tmp/x", pattern="rm *"):
    return HitlRequest(
        question_id=tool_call_id,
        thread_id="t1",
        payload=ApproveRequest(
            tool_name="execute",
            tool_call_id=tool_call_id,
            args={"command": command},
            details={"matched_pattern": pattern, "command": command},
        ),
        created_at=1000.0,
        timeout_seconds=180.0,
    )


def test_request_event_maps_to_confirm_request():
    out = convert_agent_event_to_sse(HitlRequestEvent(request=_approve_request()))
    assert len(out) == 1
    ev = out[0]
    assert ev["type"] == "sandbox_confirm_request"
    assert ev["tool_call_id"] == "call_1"
    assert ev["command"] == "rm -rf /tmp/x"
    assert ev["matched_pattern"] == "rm *"
    assert ev["timeout_seconds"] == 180.0
    assert ev["created_at"] == 1000.0


def test_answer_event_approved():
    out = convert_agent_event_to_sse(
        HitlAnswerEvent(question_id="call_1", answer=ApproveAnswer(decision="approve"))
    )
    assert out == [{"type": "sandbox_confirm_resolved",
                    "tool_call_id": "call_1", "outcome": "approved", "reason": None}]


def test_answer_event_denied_with_reason():
    out = convert_agent_event_to_sse(
        HitlAnswerEvent(question_id="call_1",
                        answer=ApproveAnswer(decision="deny", reason="nope"))
    )
    assert out[0]["outcome"] == "denied"
    assert out[0]["reason"] == "nope"


def test_answer_event_timed_out():
    out = convert_agent_event_to_sse(
        HitlAnswerEvent(question_id="call_1", answer=None, timed_out=True)
    )
    assert out[0]["outcome"] == "timed_out"


def test_answer_event_cancelled():
    out = convert_agent_event_to_sse(
        HitlAnswerEvent(question_id="call_1", answer=None, cancelled=True)
    )
    assert out[0]["outcome"] == "cancelled"
```

- [ ] **Step 2: Confirm the cubepi event/type field names match the test**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run python -c "
from cubepi.agent.types import HitlRequestEvent, HitlAnswerEvent
from cubepi.hitl.types import HitlRequest, ApproveRequest
from cubepi.hitl import ApproveAnswer
print(HitlAnswerEvent.model_fields.keys())
print(HitlRequest.model_fields.keys())
print(ApproveRequest.model_fields.keys())
"
```
Expected: `HitlAnswerEvent` has `question_id, answer, cancelled, timed_out`;
`HitlRequest` has `question_id, thread_id, payload, created_at, timeout_seconds`;
`ApproveRequest` has `tool_name, tool_call_id, args, details` (+ `kind`). If a
name differs in the installed cubepi, adjust the Step-1 test and Step-3 code to
the real names before proceeding.

- [ ] **Step 3: Run the test to verify it fails**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_stream_hitl_events.py -q
```
Expected: FAIL — events are currently dropped (empty list returned).

- [ ] **Step 4: Add the import and the two cases**

In `backend/cubeplex/agents/stream.py`, add to the cubepi event imports:
```python
from cubepi.agent.types import HitlAnswerEvent, HitlRequestEvent
```
Inside `convert_agent_event_to_sse`, before the final "drop everything else"
return, add:
```python
    if isinstance(evt, HitlRequestEvent):
        payload = evt.request.payload
        if getattr(payload, "kind", None) != "approve" or payload.tool_name != "execute":
            return []
        details = payload.details or {}
        return [
            {
                "type": "sandbox_confirm_request",
                "tool_call_id": payload.tool_call_id,
                "command": details.get("command", payload.args.get("command")),
                "matched_pattern": details.get("matched_pattern"),
                "timeout_seconds": evt.request.timeout_seconds,
                "created_at": evt.request.created_at,
            }
        ]

    if isinstance(evt, HitlAnswerEvent):
        if evt.timed_out:
            outcome = "timed_out"
        elif evt.cancelled:
            outcome = "cancelled"
        elif evt.answer is not None and evt.answer.decision == "approve":
            outcome = "approved"
        elif evt.answer is not None and evt.answer.decision == "deny":
            outcome = "denied"
        else:
            return []
        reason = getattr(evt.answer, "reason", None) if evt.answer is not None else None
        return [
            {
                "type": "sandbox_confirm_resolved",
                "tool_call_id": evt.question_id,
                "outcome": outcome,
                "reason": reason,
            }
        ]
```

- [ ] **Step 5: Run the tests to green + type check**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/unit/test_stream_hitl_events.py -q && uv run mypy cubeplex/agents/stream.py
```
Expected: PASS / `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/cubeplex/agents/stream.py backend/tests/unit/test_stream_hitl_events.py && git commit -m "feat(sandbox): translate HITL request/answer events to SSE"
```

---

## Task 7: Backend E2E — confirm pause/approve/deny/timeout

**Files:**
- Test: `backend/tests/e2e/test_sandbox_confirm_hitl.py` (create)

Drives a real cubepi run with a `confirm` rule and a real sandbox, asserting the
four outcomes. Replaces the old confirm-as-deny E2E from the #152 batch.

- [ ] **Step 1: Find the existing sandbox-policy E2E to copy harness from**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && ls tests/e2e | grep -i sandbox
```
Expected: at least the command-deny E2E from #152 (e.g.
`test_*command_deny*` / ownership-isolation tests). Open the deny one — it shows
how to: start a run with a configured command rule, drive `execute`, and read
the SSE stream / tool_result. Reuse its fixtures (sandbox provisioning, org
policy seeding, SSE client).

- [ ] **Step 2: Write the E2E**

Create `backend/tests/e2e/test_sandbox_confirm_hitl.py` modeled on the deny
E2E. Cover four cases (share one helper that seeds a `confirm` rule on a
pattern the test command matches, then starts a run that issues that command):

1. **request emitted** — assert an SSE `sandbox_confirm_request` arrives with
   the right `tool_call_id` + `command` + `matched_pattern`.
2. **approve runs it** — POST
   `/api/v1/ws/{ws}/conversations/{cid}/sandbox-confirm/{tool_call_id}`
   `{"decision":"approve"}`; assert the command actually executed (its side
   effect / output is present) and an SSE `sandbox_confirm_resolved`
   `outcome="approved"` arrives.
3. **deny skips it** — POST `{"decision":"deny","reason":"no"}`; assert the
   tool_result is an error ("denied by user") and the command did NOT execute;
   SSE `sandbox_confirm_resolved` `outcome="denied"`.
4. **timeout denies it** — start a run whose channel uses a short timeout, send
   no answer, assert a deny-shaped tool_result and SSE
   `sandbox_confirm_resolved` `outcome="timed_out"`.

For case 4, override the timeout without waiting 180s: seed the run via a fixture
that monkeypatches `InMemoryChannel(default_timeout=...)` to ~1.5s for the test
(or set the per-call `timeout` the middleware passes — prefer a fixture-level
monkeypatch of the `180.0` constant in `SandboxMiddleware.before_tool_call` to a
small value so production code is untouched). Document the chosen mechanism in a
test comment.

Use the run's actual `run_id` for the POST: capture it from the run-start SSE
event the existing E2E already reads (the deny E2E shows where `run_id` surfaces
to the client).

- [ ] **Step 3: Run the E2E**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest tests/e2e/test_sandbox_confirm_hitl.py -q
```
Expected: all four cases PASS. (Needs the worktree's `.env` +
`config.development.local.yaml`; if a sandbox provider/rustfs prerequisite is
missing the test should skip with a clear reason, not silently pass — mirror how
the existing sandbox E2E guards prerequisites.)

- [ ] **Step 4: Remove/replace the obsolete confirm-as-deny assertion**

If a prior E2E asserts the old "requires confirmation; not yet supported"
message, delete that assertion/test (its behavior is gone). Confirm nothing else
references that string:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && grep -rn "not yet supported in this deployment" tests cubeplex || echo "clean"
```
Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add backend/tests/e2e/ && git commit -m "test(sandbox): E2E for confirm HITL approve/deny/timeout"
```

---

## Task 8: Frontend SSE event types

**Files:**
- Modify: `frontend/packages/core/src/types/sse-events.ts`

**Note:** frontend uses **pnpm**; `@cubeplex/core` must build before web sees the
new types.

- [ ] **Step 1: Find the existing SSE event type union**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && grep -n "type\b" packages/core/src/types/sse-events.ts | head -40
```
Identify the discriminated union of SSE event types (each has a `type` literal)
and how existing events (e.g. tool-call events) are shaped.

- [ ] **Step 2: Add the two event interfaces + union members**

In `sse-events.ts`, following the existing event-shape convention, add:
```typescript
export interface SandboxConfirmRequestEvent {
  type: "sandbox_confirm_request";
  tool_call_id: string;
  command: string;
  matched_pattern: string | null;
  timeout_seconds: number | null;
  created_at: number;
}

export interface SandboxConfirmResolvedEvent {
  type: "sandbox_confirm_resolved";
  tool_call_id: string;
  outcome: "approved" | "denied" | "timed_out" | "cancelled";
  reason: string | null;
}
```
Add both to the SSE event union type (wherever the other events are unioned).

- [ ] **Step 3: Build core**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/core build
```
Expected: builds clean.

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add frontend/packages/core/src/types/sse-events.ts && git commit -m "feat(sandbox-ui): SSE event types for confirm request/resolved"
```

---

## Task 9: Frontend API client

**Files:**
- Create: `frontend/packages/web/src/lib/api/sandbox-confirm.ts`

- [ ] **Step 1: Find an existing scoped POST helper to mirror**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && grep -rln "conversations/" packages/web/src/lib/api | head
```
Open one (e.g. the cancel/steer client) to copy the fetch wrapper, base-URL
handling, CSRF header, and error convention.

- [ ] **Step 2: Write the client**

Create `frontend/packages/web/src/lib/api/sandbox-confirm.ts`:
```typescript
import { apiFetch } from "./client"; // use whatever the cancel client imports

export async function postSandboxConfirm(
  workspaceId: string,
  conversationId: string,
  toolCallId: string,
  body: { decision: "approve" | "deny"; reason?: string },
): Promise<void> {
  await apiFetch(
    `/api/v1/ws/${workspaceId}/conversations/${conversationId}/sandbox-confirm/${toolCallId}`,
    { method: "POST", body: JSON.stringify(body) },
  );
}
```
Adjust the import + call to match the actual fetch helper the cancel client uses
(CSRF, JSON headers, error handling come from that shared helper — do not
re-implement).

- [ ] **Step 3: Type check**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/web typecheck
```
Expected: no type errors. (If the script name differs, use the one in
`packages/web/package.json`.)

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add frontend/packages/web/src/lib/api/sandbox-confirm.ts && git commit -m "feat(sandbox-ui): API client for submitting confirm answers"
```

---

## Task 10: `SandboxConfirmCard` component

**Files:**
- Create: `frontend/packages/web/src/components/chat/SandboxConfirmCard.tsx`

Inline card: command + matched pattern, live countdown, approve/deny, optional
deny reason, resolved status strip. Risk accent (amber/red). Matches existing
tool-call card design language (per project frontend-design discipline).

- [ ] **Step 1: Find the existing tool-call card to match style**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && ls packages/web/src/components/chat
```
Open the tool-call/message card component(s) to reuse the card shell, spacing,
mono font for commands, and shadcn primitives already in use (Button, etc.).

- [ ] **Step 2: Write the component**

Create `SandboxConfirmCard.tsx`. Props:
```typescript
interface SandboxConfirmCardProps {
  toolCallId: string;
  command: string;
  matchedPattern: string | null;
  timeoutSeconds: number | null;
  createdAt: number; // epoch seconds from the request event
  resolved?: { outcome: "approved" | "denied" | "timed_out" | "cancelled"; reason: string | null };
  onApprove: () => void;
  onDeny: (reason: string) => void;
}
```
Behavior:
- While `resolved` is undefined: show command (mono), `matchedPattern` as a
  small "matched rule" tag, a countdown derived from
  `createdAt + timeoutSeconds - now` ticking each second (stop at 0), an
  **Approve** button and a **Deny** button; Deny reveals an optional reason
  input then confirms. Disable both buttons immediately after a click (pending).
- When `resolved` is set: render a non-interactive status strip — green
  "Approved", red "Denied" (+ reason if any), grey "Timed out" / "Cancelled".
- Amber accent border/background while pending; red when denied/timed out.

Use shadcn `Button` and existing card primitives; do not introduce a new design
system.

- [ ] **Step 3: Type check + lint**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/web typecheck && pnpm --filter @cubeplex/web lint
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add frontend/packages/web/src/components/chat/SandboxConfirmCard.tsx && git commit -m "feat(sandbox-ui): SandboxConfirmCard inline approve/deny card"
```

---

## Task 11: Wire the card into the chat stream

**Files:**
- Modify: the chat stream/timeline component under
  `frontend/packages/web/src/components/chat/` (the one that consumes SSE events)

- [ ] **Step 1: Find where SSE events are reduced into the timeline**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && grep -rln "tool_call\|event.type\|sse" packages/web/src/components/chat packages/web/src/hooks 2>/dev/null | head
```
Identify the reducer/handler that maps incoming SSE events to rendered timeline
items (where tool-call events become cards).

- [ ] **Step 2: Handle `sandbox_confirm_request`**

On `sandbox_confirm_request`, insert a `SandboxConfirmCard` timeline item keyed
by `tool_call_id`, storing `{command, matchedPattern, timeoutSeconds, createdAt}`
and `resolved: undefined`. Wire `onApprove` / `onDeny` to
`postSandboxConfirm(workspaceId, conversationId, toolCallId, ...)` from Task 9
(pull `workspaceId` / `conversationId` from the same context the cancel button
uses).

- [ ] **Step 3: Handle `sandbox_confirm_resolved`**

On `sandbox_confirm_resolved`, find the card item by `tool_call_id` and set its
`resolved = { outcome, reason }`. If no matching card exists (e.g. late
reconnect), ignore.

- [ ] **Step 4: Build web + type check**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/web typecheck && pnpm --filter @cubeplex/web build
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add frontend/packages/web/src/ && git commit -m "feat(sandbox-ui): render confirm card from SSE in chat stream"
```

---

## Task 12: Frontend E2E + pre-PR sweep

**Files:**
- Test: `frontend/packages/web/e2e/sandbox-confirm.spec.ts` (create; match the
  actual e2e dir/convention)

- [ ] **Step 1: Find the Playwright e2e convention**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && ls packages/web/e2e 2>/dev/null || ls packages/web/tests 2>/dev/null
```
Open a chat-flow spec to copy login/setup, how it starts a conversation, and how
it waits on streamed UI.

- [ ] **Step 2: Write the spec**

Create the spec: start a conversation that triggers a `confirm`-matched command,
assert the `SandboxConfirmCard` renders with the command text, click **Approve**,
assert the card flips to "Approved" and the command's result appears; in a
second flow click **Deny** and assert "Denied". (Use the worktree URL
`http://localhost:3090` from `.worktree.env` / `BASE_URL`.)

- [ ] **Step 3: Run the spec**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/web exec playwright test sandbox-confirm
```
Expected: PASS (backend must be running on 8090; start it per quick-reference if
needed). If browsers aren't installed: `npx playwright install` first.

- [ ] **Step 4: Pre-PR full sweep (backend)**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/backend && uv run pytest -q && uv run mypy cubeplex/
```
Expected: all pass / `Success`.

- [ ] **Step 5: Pre-PR sweep (frontend)**

Run:
```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl/frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web typecheck && pnpm --filter @cubeplex/web lint && pnpm --filter @cubeplex/web build
```
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/sandbox-confirm-hitl && git add frontend/ && git commit -m "test(sandbox-ui): E2E for confirm card approve/deny"
```

---

## PR sequencing (after execution)

1. Open the **backend PR** (Tasks 1–7) first; run the `pr-codex-review-loop`
   skill until clean; ensure CI green; merge.
2. Open the **frontend PR** (Tasks 8–12) against the frozen contract; same
   review loop; merge.
3. Clean up the worktree per `finishing-a-development-branch` after both merge.

## Acceptance criteria check (spec §9)

- [ ] Saved `confirm` rule pauses the `execute` call (Task 2 + 7 case 1/2).
- [ ] Approve runs; deny errors without running (Task 7 case 2/3).
- [ ] 180s timeout → deny-shaped result + `timed_out` trace (Task 2 + 7 case 4).
- [ ] Sandbox TTL not paused (structural: gate is in `before_tool_call`, sandbox
      `execute()` never starts — Task 2; note in PR description).
- [ ] cubepi checkpointer starts on v2 schema (Task 1 steps 5/11).
