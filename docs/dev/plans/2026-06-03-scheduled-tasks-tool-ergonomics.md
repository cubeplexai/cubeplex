# Scheduled-tasks tool ergonomics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `scheduled_tasks` agent capability hard to misuse on the first call, by encoding conditional schedule requirements in the pydantic schema, aligning `UpdateInput` with the `CreateInput` `target` sentinel, and rewriting per-operation descriptions to include canonical example payloads.

**Architecture:** All changes live in `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py` and the new unit-test module beside it. The capability builder (`backend/cubebox/agents/actions/builder.py`), the route layer (`backend/cubebox/api/routes/v1/ws_scheduled_tasks.py`), the API-facing schemas (`ScheduledTaskCreate / ScheduledTaskUpdate`), the service (`backend/cubebox/services/scheduled_task.py`) and the DB model (`backend/cubebox/models/scheduled_task.py`) are unchanged — the agent-tool layer just flattens its nested union into the same `dict[str, Any]` shape the service already accepts.

**Tech Stack:** Python 3.12 / pydantic v2 / pytest / uv / pre-commit (mypy strict, ruff, line length 100).

**Worktree:** `/home/chris/cubebox/.worktrees/feat/sched-tasks-tool-ergo` on branch `feat/sched-tasks-tool-ergo` (slot 2: backend port 8002, DB `cubebox_feat_sched_tasks_tool_ergo`). Run all commands from this worktree root. Source the worktree env if you need ports: `cat .worktree.env`.

**Spec:** `docs/dev/specs/2026-06-03-scheduled-tasks-tool-ergonomics-design.md`

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py` | Modify | Replace flat `CreateInput` / `UpdateInput` schedule fields with a nested `Schedule = Cron \| Interval \| Once` discriminated union. Replace `UpdateInput.target_mode` + `target_conversation_id` with a sentinel `target` mirroring `CreateInput`. Update `_handle_create` and `_handle_update` to flatten the nested union back into the service `dict`. Rewrite every `AgentOperation.description` with a one-line minimal example payload, and rewrite the capability description to point at them. |
| `backend/tests/unit/test_scheduled_tasks_capability.py` | Create | Direct unit tests for the agent-tool input models and handler translations: schedule union happy paths and rejection cases, `target` sentinel translation in create and update, description content sanity check. |
| `backend/tests/unit/test_scheduled_task_service.py` | Unchanged | Service-layer tests stay green because the handler still emits the same flat `dict` shape. |
| `backend/tests/e2e/test_scheduled_tasks_api.py` | Unchanged | API routes are unchanged. |
| `backend/tests/e2e/test_scheduled_tasks_firing.py` | Unchanged | Firing path is unchanged. |
| `docs/dev/specs/2026-06-03-scheduled-tasks-tool-ergonomics-design.md` | Reference | Frozen design doc. Do not edit during implementation. |

---

## Task 1: Nested `Schedule` discriminated union for `CreateInput`

**Files:**
- Create: `backend/tests/unit/test_scheduled_tasks_capability.py`
- Modify: `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py:61-86` (replace `CreateInput`)
- Modify: `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py:145-171` (rewrite `_handle_create`)

- [ ] **Step 1.1: Write the failing tests**

Create `backend/tests/unit/test_scheduled_tasks_capability.py`:

```python
"""Unit tests for the scheduled_tasks agent capability input models and handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from cubebox.agents.actions.capabilities.scheduled_tasks import (
    SCHEDULED_TASKS_CAPABILITY,
    CreateInput,
    UpdateInput,
    _handle_create,
    _handle_update,
)
from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput


def _ctx(conversation_id: str | None = "conv-test") -> ScopeContext:
    return ScopeContext(
        org_id="org-test",
        workspace_id="ws-test",
        user_id="usr-test",
        conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# CreateInput.schedule — nested discriminated union
# ---------------------------------------------------------------------------


def test_create_cron_schedule_parses() -> None:
    inp = CreateInput(
        name="morning-reply",
        prompt="reply to bigv",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
    )
    assert inp.schedule.kind == "cron"
    assert inp.schedule.cron_expr == "0 9 * * *"
    assert inp.schedule.timezone == "UTC"


def test_create_interval_schedule_parses() -> None:
    inp = CreateInput(
        name="poll",
        prompt="poll feed",
        schedule={"kind": "interval", "interval_seconds": 1800},
    )
    assert inp.schedule.kind == "interval"
    assert inp.schedule.interval_seconds == 1800


def test_create_once_schedule_parses() -> None:
    inp = CreateInput(
        name="remind",
        prompt="remind",
        schedule={"kind": "once", "run_at": "2026-06-10T15:00:00+00:00"},
    )
    assert inp.schedule.kind == "once"
    assert inp.schedule.run_at == datetime(2026, 6, 10, 15, 0, tzinfo=UTC)


def test_create_cron_without_cron_expr_rejected_at_parse() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "cron"},
        )
    # The pydantic error must name the missing field at the schedule level.
    assert "cron_expr" in str(exc_info.value)


def test_create_interval_without_seconds_rejected_at_parse() -> None:
    with pytest.raises(ValidationError):
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "interval"},
        )


def test_create_once_without_run_at_rejected_at_parse() -> None:
    with pytest.raises(ValidationError):
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "once"},
        )


def test_create_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "yearly", "cron_expr": "0 0 1 1 *"},
        )


# ---------------------------------------------------------------------------
# _handle_create — flattens nested union into the service dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_create_cron_flattens_to_service_dict() -> None:
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        task = AsyncMock()
        task.id = "stask-1"
        task.name = data["name"]
        task.status = "active"
        task.schedule_kind = data["schedule_kind"]
        task.cron_expr = data.get("cron_expr")
        task.interval_seconds = data.get("interval_seconds")
        task.timezone = data.get("timezone", "UTC")
        task.prompt = data["prompt"]
        task.target_mode = data["target_mode"]
        task.next_fire_at = datetime(2026, 6, 4, 9, 0, tzinfo=UTC)
        task.last_fired_at = None
        return task

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    cap._svc.create = fake_create  # type: ignore[method-assign]

    inp = CreateInput(
        name="morning-reply",
        prompt="reply to bigv",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
    )
    await _handle_create(_ctx(), AsyncMock(), inp)

    assert captured["schedule_kind"] == "cron"
    assert captured["cron_expr"] == "0 9 * * *"
    assert captured["interval_seconds"] is None
    assert captured["run_at"] is None
    assert captured["timezone"] == "Asia/Shanghai"
    assert captured["target_mode"] == "new_each_run"
    assert captured["target_conversation_id"] is None


@pytest.mark.asyncio
async def test_handle_create_target_current_conversation_uses_ctx_id() -> None:
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        task = AsyncMock()
        task.id = "stask-1"
        task.name = data["name"]
        task.status = "active"
        task.schedule_kind = data["schedule_kind"]
        task.cron_expr = data.get("cron_expr")
        task.interval_seconds = data.get("interval_seconds")
        task.timezone = "UTC"
        task.prompt = data["prompt"]
        task.target_mode = data["target_mode"]
        task.next_fire_at = None
        task.last_fired_at = None
        return task

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    cap._svc.create = fake_create  # type: ignore[method-assign]

    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    await _handle_create(_ctx("conv-abc"), AsyncMock(), inp)

    assert captured["target_mode"] == "fixed"
    assert captured["target_conversation_id"] == "conv-abc"


@pytest.mark.asyncio
async def test_handle_create_target_current_conversation_without_ctx_raises() -> None:
    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    with pytest.raises(ActionInvalidInput, match="current_conversation"):
        await _handle_create(_ctx(conversation_id=None), AsyncMock(), inp)
```

- [ ] **Step 1.2: Run the tests, verify they fail**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q
```

Expected: ALL fail — `CreateInput` doesn't accept `schedule={...}` yet, and `_handle_create` doesn't know how to flatten it. Error is an `ImportError` for `CreateInput` (it exists) plus pydantic `extra_forbidden` or "field required" on missing flat fields.

- [ ] **Step 1.3: Refactor `CreateInput` to use nested `Schedule`**

Edit `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py`:

Replace the entire `CreateInput` block (lines 61-86 in the current file) and the imports section (line 6) so the module top looks like:

```python
"""Scheduled-tasks agent capability — 8 operations for CRUD and lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput, AgentCapability, AgentOperation
from cubebox.services.scheduled_task import ScheduledTaskService
from cubebox.utils.time import utc_isoformat
```

Then, replace `class CreateInput` (lines 61-86 in the original) with:

```python
class CronSchedule(BaseModel):
    """Recurring schedule defined by a cron expression."""

    kind: Literal["cron"]
    cron_expr: str = Field(
        description="5-field cron expression in the given timezone. Example: '0 9 * * *'.",
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone name, e.g. 'America/New_York'. Defaults to UTC.",
    )


class IntervalSchedule(BaseModel):
    """Recurring schedule that fires every N seconds, starting at create time."""

    kind: Literal["interval"]
    interval_seconds: int = Field(ge=60, description="Seconds between fires. Minimum 60.")


class OnceSchedule(BaseModel):
    """Schedule that fires exactly once at a given timestamp."""

    kind: Literal["once"]
    run_at: datetime = Field(
        description="ISO 8601 datetime (must include timezone offset) for the single fire.",
    )


Schedule = Annotated[
    Union[CronSchedule, IntervalSchedule, OnceSchedule],
    Field(discriminator="kind"),
]


class CreateInput(BaseModel):
    name: str = Field(description="Human-readable name, unique within the workspace.")
    prompt: str = Field(description="The prompt sent to the agent on every fire.")
    schedule: Schedule = Field(
        description=(
            "When to run. Discriminated by 'kind'. Examples: "
            "{'kind':'cron','cron_expr':'0 9 * * *'}, "
            "{'kind':'interval','interval_seconds':1800}, "
            "{'kind':'once','run_at':'2026-06-10T15:00:00Z'}."
        ),
    )
    target: Literal["new_each_run", "current_conversation"] = Field(
        default="new_each_run",
        description=(
            "Where the task runs. 'new_each_run' opens a fresh conversation each fire "
            "(default). 'current_conversation' binds the task to the conversation this "
            "tool was called from — you do NOT need to pass a conversation ID, the "
            "backend reads it from the call context."
        ),
    )
    end_at: datetime | None = Field(
        default=None,
        description="Optional ISO 8601 datetime after which the task stops firing.",
    )
```

- [ ] **Step 1.4: Rewrite `_handle_create` to flatten the nested union**

Replace `_handle_create` (lines 145-171 in the original file) with:

```python
async def _handle_create(ctx: ScopeContext, session: AsyncSession, inp: CreateInput) -> Any:
    if inp.target == "current_conversation":
        if ctx.conversation_id is None:
            raise ActionInvalidInput(
                "target='current_conversation' requires a conversation context; "
                "either start from within a conversation or use target='new_each_run'."
            )
        target_mode = "fixed"
        target_conversation_id: str | None = ctx.conversation_id
    else:
        target_mode = "new_each_run"
        target_conversation_id = None

    sched = inp.schedule
    data: dict[str, Any] = {
        "name": inp.name,
        "prompt": inp.prompt,
        "schedule_kind": sched.kind,
        "cron_expr": getattr(sched, "cron_expr", None),
        "interval_seconds": getattr(sched, "interval_seconds", None),
        "run_at": getattr(sched, "run_at", None),
        "timezone": getattr(sched, "timezone", "UTC"),
        "target_mode": target_mode,
        "target_conversation_id": target_conversation_id,
        "end_at": inp.end_at,
    }
    task = await _svc.create(ctx, session, data)
    return _task_summary(task)
```

- [ ] **Step 1.5: Run the tests, verify they pass**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q
```

Expected: 10 passed.

- [ ] **Step 1.6: Run the service-layer test to confirm flat-dict contract still holds**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_task_service.py -x -q
```

Expected: all green (we didn't touch the service).

- [ ] **Step 1.7: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/scheduled_tasks.py \
        backend/tests/unit/test_scheduled_tasks_capability.py
git commit -m "feat(scheduled-tasks): nest schedule into discriminated union on CreateInput"
```

---

## Task 2: Same nested `Schedule` on `UpdateInput`

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py:88-99` (rewrite `UpdateInput`)
- Modify: `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py:174-184` (rewrite `_handle_update`)
- Modify: `backend/tests/unit/test_scheduled_tasks_capability.py` (extend with UpdateInput tests)

- [ ] **Step 2.1: Add failing tests for `UpdateInput`**

Append to `backend/tests/unit/test_scheduled_tasks_capability.py`:

```python
# ---------------------------------------------------------------------------
# UpdateInput.schedule — same nested union, optional
# ---------------------------------------------------------------------------


def test_update_without_schedule_parses() -> None:
    inp = UpdateInput(task_id="stask-1", name="renamed")
    assert inp.schedule is None
    assert inp.name == "renamed"


def test_update_with_schedule_parses() -> None:
    inp = UpdateInput(
        task_id="stask-1",
        schedule={"kind": "interval", "interval_seconds": 600},
    )
    assert inp.schedule is not None
    assert inp.schedule.kind == "interval"
    assert inp.schedule.interval_seconds == 600


def test_update_cron_without_cron_expr_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateInput(task_id="stask-1", schedule={"kind": "cron"})


@pytest.mark.asyncio
async def test_handle_update_flattens_schedule() -> None:
    captured: dict = {}

    async def fake_update(ctx, session, task_id, data):  # type: ignore[no-untyped-def]
        captured["task_id"] = task_id
        captured.update(data)
        task = AsyncMock()
        task.id = task_id
        task.name = data.get("name") or "n"
        task.status = "active"
        task.schedule_kind = data.get("schedule_kind") or "interval"
        task.cron_expr = data.get("cron_expr")
        task.interval_seconds = data.get("interval_seconds") or 600
        task.timezone = "UTC"
        task.prompt = "p"
        task.target_mode = "new_each_run"
        task.next_fire_at = None
        task.last_fired_at = None
        return task

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    cap._svc.update = fake_update  # type: ignore[method-assign]

    inp = UpdateInput(
        task_id="stask-1",
        schedule={"kind": "interval", "interval_seconds": 600},
    )
    await _handle_update(_ctx(), AsyncMock(), inp)

    assert captured["task_id"] == "stask-1"
    assert captured["schedule_kind"] == "interval"
    assert captured["interval_seconds"] == 600
    # Untouched fields must NOT be in the data dict (so the service's
    # "None means skip" loop leaves them alone).
    assert "name" not in captured
    assert "prompt" not in captured
```

- [ ] **Step 2.2: Run the failing tests**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q -k update
```

Expected: failures on `UpdateInput` still using `schedule_kind` / `cron_expr` flat fields.

- [ ] **Step 2.3: Rewrite `UpdateInput`**

Replace the `UpdateInput` class (lines 88-99 in original) with:

```python
class UpdateInput(BaseModel):
    task_id: str
    name: str | None = None
    prompt: str | None = None
    schedule: Schedule | None = Field(
        default=None,
        description=(
            "Replace the schedule whole. Same discriminated shape as create. "
            "Example: {'kind':'cron','cron_expr':'0 10 * * *'}. Omit to keep the "
            "current schedule."
        ),
    )
    target: Literal["new_each_run", "current_conversation"] | None = Field(
        default=None,
        description=(
            "Same semantics as on create. Omit to leave the target unchanged. "
            "'current_conversation' resolves to the conversation this tool was "
            "called from — no ID needed."
        ),
    )
    end_at: datetime | None = None
```

- [ ] **Step 2.4: Rewrite `_handle_update`**

Replace `_handle_update` (lines 174-184 in original) with:

```python
async def _handle_update(ctx: ScopeContext, session: AsyncSession, inp: UpdateInput) -> Any:
    # Only emit fields the caller explicitly set; the service's update loop
    # treats absent / None as "leave alone", so we never null-out untouched
    # columns by accident.
    set_fields = inp.model_fields_set
    data: dict[str, Any] = {}

    if "name" in set_fields and inp.name is not None:
        data["name"] = inp.name
    if "prompt" in set_fields and inp.prompt is not None:
        data["prompt"] = inp.prompt
    if "end_at" in set_fields:
        # end_at supports explicit null clearing — pass through as-is.
        data["end_at"] = inp.end_at

    if "schedule" in set_fields and inp.schedule is not None:
        sched = inp.schedule
        data["schedule_kind"] = sched.kind
        if sched.kind == "cron":
            data["cron_expr"] = sched.cron_expr
            data["timezone"] = sched.timezone
        elif sched.kind == "interval":
            data["interval_seconds"] = sched.interval_seconds
        elif sched.kind == "once":
            data["run_at"] = sched.run_at

    if "target" in set_fields and inp.target is not None:
        if inp.target == "current_conversation":
            if ctx.conversation_id is None:
                raise ActionInvalidInput(
                    "target='current_conversation' requires a conversation context; "
                    "either start from within a conversation or use target='new_each_run'."
                )
            data["target_mode"] = "fixed"
            data["target_conversation_id"] = ctx.conversation_id
        else:
            data["target_mode"] = "new_each_run"
            data["target_conversation_id"] = None

    task = await _svc.update(ctx, session, inp.task_id, data)
    return _task_summary(task)
```

- [ ] **Step 2.5: Run the tests, verify they pass**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q
```

Expected: all tests pass (Task 1 + Task 2 combined).

- [ ] **Step 2.6: Run the service tests, the schema tests, and the scheduled-task E2E to confirm nothing else regressed**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_task_service.py tests/unit/test_scheduled_task_schemas.py -q
uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py -q
```

Expected: all green. The E2E hits the HTTP layer (`ScheduledTaskCreate / Update`), which is untouched.

- [ ] **Step 2.7: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/scheduled_tasks.py \
        backend/tests/unit/test_scheduled_tasks_capability.py
git commit -m "feat(scheduled-tasks): mirror nested schedule + target sentinel on UpdateInput"
```

---

## Task 3: Rewrite per-operation descriptions with canonical examples

**Files:**
- Modify: `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py:206-283` (rewrite `SCHEDULED_TASKS_CAPABILITY`)
- Modify: `backend/tests/unit/test_scheduled_tasks_capability.py` (add description-content sanity tests)

- [ ] **Step 3.1: Write the failing description tests**

Append to `backend/tests/unit/test_scheduled_tasks_capability.py`:

```python
# ---------------------------------------------------------------------------
# Description content — each operation carries a copyable example payload
# ---------------------------------------------------------------------------


def _op(name: str):  # type: ignore[no-untyped-def]
    for op in SCHEDULED_TASKS_CAPABILITY.operations:
        if op.name == name:
            return op
    raise AssertionError(f"op {name!r} not registered")


def test_each_operation_description_contains_example_payload() -> None:
    # Every non-trivial op should show a JSON-shaped example the model can copy.
    for op_name in ("create", "update", "pause", "resume", "delete", "get", "list_runs"):
        desc = _op(op_name).description
        assert "Example" in desc or "example" in desc, f"{op_name} description has no example"
        assert '"operation"' in desc, f"{op_name} description omits the operation discriminator"


def test_create_description_documents_all_three_schedule_kinds() -> None:
    desc = _op("create").description
    for keyword in ("cron", "interval", "once"):
        assert keyword in desc, f"create description omits schedule kind {keyword!r}"


def test_create_description_documents_target_sentinel() -> None:
    desc = _op("create").description
    assert "current_conversation" in desc
    assert "do not need" in desc.lower() or "no id" in desc.lower() or "no conversation id" in desc.lower(), (
        "create description must tell the model it doesn't need to pass a conversation ID"
    )


def test_capability_description_is_short_and_points_to_operations() -> None:
    cap_desc = SCHEDULED_TASKS_CAPABILITY.description
    # Capability-level description shouldn't duplicate per-op examples (token cost).
    assert len(cap_desc) < 600
    # But it should mention that each operation has its own example.
    assert "operation" in cap_desc.lower()


def test_list_description_states_no_arguments() -> None:
    desc = _op("list").description
    assert "no arguments" in desc.lower() or "no parameters" in desc.lower()
```

- [ ] **Step 3.2: Run the failing tests**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q -k description
```

Expected: fail — current descriptions don't have JSON examples.

- [ ] **Step 3.3: Rewrite the capability + operation descriptions**

Replace the `SCHEDULED_TASKS_CAPABILITY = AgentCapability(...)` block (lines 206-283 in the original file) with:

```python
SCHEDULED_TASKS_CAPABILITY = AgentCapability(
    name="scheduled_tasks",
    description=(
        "Manage scheduled tasks in the current workspace. Each task runs a prompt on "
        "a cron, interval, or one-shot schedule. The discriminator field is "
        "`operation` (one of: list, get, list_runs, create, update, pause, resume, "
        "delete). Every operation has an example payload in its own description — "
        "use those. Only mutate tasks (create / update / pause / resume / delete) "
        "when the user has explicitly asked you to."
    ),
    operations=[
        AgentOperation(
            name="list",
            description=(
                "List all scheduled tasks in the workspace. Takes no arguments. "
                'Example: {"operation":"list"}'
            ),
            input_model=ListInput,
            handler=_handle_list,
            mutates=False,
        ),
        AgentOperation(
            name="get",
            description=(
                "Get details for a single scheduled task by ID. "
                'Example: {"operation":"get","task_id":"stask-1gBGEPTNA5c1Ou"}'
            ),
            input_model=GetInput,
            handler=_handle_get,
            mutates=False,
        ),
        AgentOperation(
            name="list_runs",
            description=(
                "List recent execution history for a scheduled task. "
                'Example: {"operation":"list_runs","task_id":"stask-1gBGEPTNA5c1Ou"}'
            ),
            input_model=ListRunsInput,
            handler=_handle_list_runs,
            mutates=False,
        ),
        AgentOperation(
            name="create",
            description=(
                "Create a new scheduled task. The `schedule` field is a discriminated "
                "object keyed by `kind` (cron | interval | once); pass the fields that "
                "go with the chosen kind. Examples:\n"
                "  cron daily 09:00 UTC:\n"
                '    {"operation":"create","name":"morning-reply","prompt":"...",'
                '"schedule":{"kind":"cron","cron_expr":"0 9 * * *"}}\n'
                "  every 30 minutes:\n"
                '    {"operation":"create","name":"poll","prompt":"...",'
                '"schedule":{"kind":"interval","interval_seconds":1800}}\n'
                "  one-shot at a specific time:\n"
                '    {"operation":"create","name":"remind","prompt":"...",'
                '"schedule":{"kind":"once","run_at":"2026-06-10T15:00:00Z"}}\n'
                "To bind the task to the conversation this tool was called from, add "
                '`"target":"current_conversation"`. You do not need to know the '
                "conversation ID — the backend fills it in from the call context. To "
                "open a fresh conversation on each fire (default), omit `target` or "
                'pass `"new_each_run"`. Only call when the user has explicitly asked.'
            ),
            input_model=CreateInput,
            handler=_handle_create,
            mutates=True,
        ),
        AgentOperation(
            name="update",
            description=(
                "Update fields on an existing scheduled task. Omit any field to leave "
                "it unchanged. `schedule` is replaced whole (same discriminated shape "
                "as create); there is no partial-schedule update. `target` uses the "
                "same sentinel as create. Examples:\n"
                "  rename only:\n"
                '    {"operation":"update","task_id":"stask-1gBGEPTNA5c1Ou",'
                '"name":"renamed"}\n'
                "  switch to a different cron:\n"
                '    {"operation":"update","task_id":"stask-1gBGEPTNA5c1Ou",'
                '"schedule":{"kind":"cron","cron_expr":"0 10 * * *"}}\n'
                "  pin to the current conversation:\n"
                '    {"operation":"update","task_id":"stask-1gBGEPTNA5c1Ou",'
                '"target":"current_conversation"}\n'
                "Only call when the user has explicitly asked."
            ),
            input_model=UpdateInput,
            handler=_handle_update,
            mutates=True,
        ),
        AgentOperation(
            name="pause",
            description=(
                "Pause a scheduled task so it stops firing. "
                'Example: {"operation":"pause","task_id":"stask-1gBGEPTNA5c1Ou"} '
                "Only call when the user has explicitly asked."
            ),
            input_model=PauseInput,
            handler=_handle_pause,
            mutates=True,
        ),
        AgentOperation(
            name="resume",
            description=(
                "Resume a paused scheduled task. "
                'Example: {"operation":"resume","task_id":"stask-1gBGEPTNA5c1Ou"} '
                "Only call when the user has explicitly asked."
            ),
            input_model=ResumeInput,
            handler=_handle_resume,
            mutates=True,
        ),
        AgentOperation(
            name="delete",
            description=(
                "Soft-delete a scheduled task (it will no longer fire). "
                'Example: {"operation":"delete","task_id":"stask-1gBGEPTNA5c1Ou"} '
                "Only call when the user has explicitly asked."
            ),
            input_model=DeleteInput,
            handler=_handle_delete,
            mutates=True,
        ),
    ],
)
```

- [ ] **Step 3.4: Run the description tests, verify they pass**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -x -q -k description
```

Expected: all five description tests green.

- [ ] **Step 3.5: Run the full capability test module**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -q
```

Expected: every test in this file passes.

- [ ] **Step 3.6: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities/scheduled_tasks.py \
        backend/tests/unit/test_scheduled_tasks_capability.py
git commit -m "feat(scheduled-tasks): rewrite operation descriptions with copyable example payloads"
```

---

## Task 4: Pre-PR sweep — full backend test suite + mypy

**Files:** none new.

- [ ] **Step 4.1: Run the whole backend unit suite**

```bash
cd backend
uv run pytest tests/unit -q
```

Expected: all green. If any unrelated test was already red on `main`, note it in the PR description; do not fix it here.

- [ ] **Step 4.2: Run the scheduled-tasks E2E**

```bash
cd backend
uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py -q
```

Expected: all green. The handler still emits the flat-dict shape the service / route layer expect.

- [ ] **Step 4.3: Run pre-commit on the changed files**

```bash
pre-commit run --files \
  backend/cubebox/agents/actions/capabilities/scheduled_tasks.py \
  backend/tests/unit/test_scheduled_tasks_capability.py
```

Expected: green. Per project policy, every commit must type-check the whole repo cleanly — fix any mypy / ruff issues that surface and amend before pushing.

If pre-commit reformats, re-run the unit tests to confirm and commit the formatting fix as a separate commit if needed:

```bash
cd backend
uv run pytest tests/unit/test_scheduled_tasks_capability.py -q
git add -u && git commit -m "style: pre-commit fixups"
```

- [ ] **Step 4.4: Smoke check the generated JSON Schema by eye**

```bash
cd backend
uv run python -c "
from cubebox.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubebox.agents.actions.builder import build_capability_tool

class _FakeCtx:
    async def __aenter__(self): return (None, None)
    async def __aexit__(self, *a): return False

def _factory(): return _FakeCtx()

tool = build_capability_tool(SCHEDULED_TASKS_CAPABILITY, _factory, allow_mutations=True)
import json
print(json.dumps(tool.parameters.model_json_schema(), indent=2)[:4000])
"
```

Expected: each `Op_create` branch shows `schedule` as a oneOf of three objects, each requiring its kind-specific field. Eyeball confirm — paste a snippet into the PR description so reviewers can see what the model now sees.

---

## Task 5: Push branch and open PR

**Files:** none.

- [ ] **Step 5.1: Push the branch**

```bash
git push -u origin feat/sched-tasks-tool-ergo
```

- [ ] **Step 5.2: Open the PR**

Use a HEREDOC body summarising the spec + this plan + the smoke-check snippet from Step 4.4. Include the trace ID (`7acc0ec0c3eeb7a78260fa52a801b363`) as the failing case this PR addresses, and link the spec file at `docs/dev/specs/2026-06-03-scheduled-tasks-tool-ergonomics-design.md`. Example outline:

```
gh pr create --title "feat(scheduled-tasks): make agent tool hard to misuse on first call" \
  --body "$(cat <<'EOF'
## Summary
- Nest `schedule` into a `Cron | Interval | Once` discriminated union on both
  CreateInput and UpdateInput, so "cron requires cron_expr" lives in the
  schema instead of a runtime check.
- Mirror the `target='current_conversation'` sentinel from CreateInput onto
  UpdateInput, so update path no longer leaks `target_mode + target_conversation_id`
  to the LLM.
- Rewrite every operation description with a copyable JSON example payload
  (including the `target` semantics on create / update).

Spec: docs/dev/specs/2026-06-03-scheduled-tasks-tool-ergonomics-design.md
Plan: docs/dev/plans/2026-06-03-scheduled-tasks-tool-ergonomics.md
Failing trace this fixes: 7acc0ec0c3eeb7a78260fa52a801b363

## Test plan
- [x] tests/unit/test_scheduled_tasks_capability.py — new
- [x] tests/unit/test_scheduled_task_service.py — unchanged, still green
- [x] tests/unit/test_scheduled_task_schemas.py — unchanged, still green
- [x] tests/e2e/test_scheduled_tasks_api.py — unchanged, still green
- [x] tests/e2e/test_scheduled_tasks_firing.py — unchanged, still green
- [ ] Live verify: re-pose the failing prompt in a dev workspace against
      kimi-k2.6, attach the new trace ID to a follow-up comment.
EOF
)"
```

- [ ] **Step 5.3: Trigger the codex review loop**

Follow `.claude/skills/pr-codex-review-loop/SKILL.md`: tag `@codex` on the PR, wait for review, address comments, re-push, re-tag, repeat until clean.

---

## Out of plan / manual follow-up

- **Live verify against a real LLM call.** Stand up the worktree backend and frontend, send the prompt "你先创建这些定时任务，让这些任务都在这个会话运行" to kimi-k2.6, confirm the first `scheduled_tasks` call succeeds, paste the new trace ID into the PR.

```bash
# In one shell — backend on the worktree's allocated port
cd backend && uv run python main.py
# In another — frontend, env-wrapped so it picks up port 3002 / 8002
cd frontend && pnpm dev
```

- **Cubepi-side ValidationError translation.** Separate PR in `/home/chris/cubepi`: edit `cubepi/agent/tools.py:114-117` to format pydantic errors (field paths, discriminator + allowed values) before returning them to the LLM. Bump the cubepi pin in cubebox after merge. Tracked in the spec under "Out of scope".

- **Progressive schema disclosure for capability tools.** Designed alongside MCP, separate spec.

---

## Self-Review

**Spec coverage:**
- Spec §1 (per-op examples + `target` doc) → Task 3.
- Spec §2 (ValidationError translation, deferred) → "Out of plan" section.
- Spec §3 (UpdateInput sentinel `target`) → Task 2.
- Spec §4 (nested `Schedule` discriminated union) → Task 1 (Create) + Task 2 (Update).
- Spec §5 (same treatment for other capabilities) → out-of-scope per spec.
- Spec "How we verify" → Tasks 1.5–1.6 / 2.5–2.6 / 3.4–3.5 (unit), Task 4.2 (E2E), Out-of-plan (live).

**Placeholder scan:** no TBD/TODO; every code step has the full code; every command has expected output.

**Type consistency:** `Schedule` is the same `Annotated[Union[CronSchedule, IntervalSchedule, OnceSchedule], Field(discriminator="kind")]` in Task 1 and reused as `Schedule | None` in Task 2. `target` is `Literal["new_each_run", "current_conversation"]` on Create (default) and `Literal[...] | None = None` on Update (omit-means-unchanged). Handler dict keys (`schedule_kind`, `cron_expr`, `interval_seconds`, `run_at`, `timezone`, `target_mode`, `target_conversation_id`, `end_at`) match the existing `ScheduledTaskService.create` / `.update` consumer.
