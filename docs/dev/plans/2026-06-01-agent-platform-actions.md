# Agent Platform Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a unified mechanism for agent-operable platform capabilities and land scheduled-task management as the first capability.

**Architecture:** Three layers — (1) `ScheduledTaskService` owns all business logic + transaction + authorization; (2) an action registry (`AgentOperation`/`AgentCapability`) + generic builder produces one cubepi `AgentTool` per capability via a Pydantic discriminated union; (3) two thin front doors (REST route and agent tool) both delegate to the service. A structural mutation gate excludes write operations from automated (non-interactive) runs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, cubepi `AgentTool`, Pydantic v2 discriminated unions, pytest.

**Spec:** `docs/dev/specs/2026-06-01-agent-platform-actions-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/cubeplex/agents/actions/__init__.py` | Package init |
| Create | `backend/cubeplex/agents/actions/context.py` | `ScopeContext` dataclass + builders |
| Create | `backend/cubeplex/agents/actions/types.py` | `AgentOperation`, `AgentCapability`, domain exceptions |
| Create | `backend/cubeplex/agents/actions/builder.py` | `build_capability_tool()` — generic factory |
| Create | `backend/cubeplex/agents/actions/registry.py` | `AGENT_CAPABILITIES` list + `tools_for_run()` |
| Create | `backend/cubeplex/agents/actions/capabilities/__init__.py` | Package init |
| Create | `backend/cubeplex/agents/actions/capabilities/scheduled_tasks.py` | Declares the `scheduled_tasks` capability |
| Create | `backend/cubeplex/services/scheduled_task.py` | `ScheduledTaskService` — source of truth |
| Modify | `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py` | Refactor to thin adapter over service |
| Modify | `backend/cubeplex/streams/run_manager.py:30` | Add `trigger` field to `RunContext` |
| Modify | `backend/cubeplex/streams/run_manager.py:540` | Thread `trigger` through `start_run` |
| Modify | `backend/cubeplex/streams/run_manager.py:1860` | Thread `trigger` through `_execute_run` |
| Modify | `backend/cubeplex/streams/run_manager.py:969` | Thread `trigger` through `_run_cubepi_path`, wire `tools_for_run` |
| Modify | `backend/cubeplex/schedules/dispatch.py:91-102` | Pass `trigger="automated"` to `RunContext` |
| Create | `backend/tests/unit/test_scheduled_task_service.py` | Service unit tests |
| Create | `backend/tests/unit/test_agent_action_builder.py` | Builder + mutation gate tests |
| Modify | `backend/tests/e2e/test_scheduled_tasks_api.py` | Existing route tests stay green (guard) |

---

### Task 1: Domain Exceptions + ScopeContext + AgentOperation/AgentCapability types

**Files:**
- Create: `backend/cubeplex/agents/actions/__init__.py`
- Create: `backend/cubeplex/agents/actions/context.py`
- Create: `backend/cubeplex/agents/actions/types.py`
- Create: `backend/cubeplex/agents/actions/capabilities/__init__.py`

These are pure data types with no dependencies on DB or services — they can be built and tested in isolation.

- [ ] **Step 1: Create the package and `context.py`**

```python
# backend/cubeplex/agents/actions/__init__.py
"""Agent platform actions — unified mechanism for agent-operable capabilities."""

# backend/cubeplex/agents/actions/capabilities/__init__.py
"""Capability declarations for agent platform actions."""
```

```python
# backend/cubeplex/agents/actions/context.py
"""Scoped context for agent platform actions."""

from __future__ import annotations

from dataclasses import dataclass

from cubeplex.models.membership import Role


@dataclass(frozen=True)
class ScopeContext:
    """Everything an operation needs to be scoped and authorized."""

    org_id: str
    workspace_id: str
    user_id: str
    role: Role
    conversation_id: str | None = None
```

- [ ] **Step 2: Create `types.py` — domain exceptions + operation/capability dataclasses**

```python
# backend/cubeplex/agents/actions/types.py
"""Core types for the agent platform actions mechanism."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from cubeplex.agents.actions.context import ScopeContext


# --- Domain exceptions (raised by services, mapped by front doors) ---

class ActionNotFound(Exception):
    """Target entity does not exist or is soft-deleted."""


class ActionPermissionDenied(Exception):
    """Caller lacks the required role (e.g. not owner or admin)."""


class ActionInvalidInput(Exception):
    """Validation failure (bad cron, missing field, etc.)."""


# --- Registry types ---

@dataclass(frozen=True)
class AgentOperation:
    """One operation within a capability (e.g. 'create', 'list')."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    mutates: bool = False


@dataclass(frozen=True)
class AgentCapability:
    """A named group of operations exposed as a single agent tool."""

    name: str
    description: str
    operations: list[AgentOperation] = field(default_factory=list)
```

- [ ] **Step 3: Verify types are importable**

Run: `cd backend && uv run python -c "from cubeplex.agents.actions.types import AgentCapability, AgentOperation, ActionNotFound; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run mypy on the new files**

Run: `cd backend && uv run mypy cubeplex/agents/actions/`
Expected: `Success: no issues found`

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/agents/actions/
git commit -m "feat(actions): add ScopeContext, domain exceptions, AgentOperation/AgentCapability types"
```

---

### Task 2: Generic capability tool builder

**Files:**
- Create: `backend/cubeplex/agents/actions/builder.py`
- Create: `backend/tests/unit/test_agent_action_builder.py`

The builder takes an `AgentCapability` + a context factory and produces one cubepi `AgentTool` with a Pydantic discriminated-union input model. It also implements the mutation gate: when `allow_mutations=False`, mutating operations are excluded.

- [ ] **Step 1: Write builder tests (mutation gate + dispatch + error mapping)**

```python
# backend/tests/unit/test_agent_action_builder.py
"""Tests for the generic capability tool builder."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from cubeplex.agents.actions.builder import build_capability_tool
from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
    AgentCapability,
    AgentOperation,
)
from cubeplex.models.membership import Role


class EchoInput(BaseModel):
    message: str


class EmptyInput(BaseModel):
    pass


def _make_scope() -> ScopeContext:
    return ScopeContext(
        org_id="org-test",
        workspace_id="ws-test",
        user_id="usr-test",
        role=Role.MEMBER,
        conversation_id="conv-test",
    )


@asynccontextmanager
async def _fake_context_factory() -> AsyncIterator[tuple[ScopeContext, Any]]:
    """Yields (ScopeContext, fake-session)."""
    yield (_make_scope(), "fake-session")


def _cap_with_ops(*, read_handler: Any = None, write_handler: Any = None) -> AgentCapability:
    ops: list[AgentOperation] = []
    if read_handler is not None:
        ops.append(
            AgentOperation(
                name="list",
                description="List items",
                input_model=EmptyInput,
                handler=read_handler,
                mutates=False,
            )
        )
    if write_handler is not None:
        ops.append(
            AgentOperation(
                name="create",
                description="Create an item",
                input_model=EchoInput,
                handler=write_handler,
                mutates=True,
            )
        )
    return AgentCapability(name="test_cap", description="Test capability", operations=ops)


class TestMutationGate:
    def test_allow_mutations_true_includes_all_ops(self) -> None:
        cap = _cap_with_ops(read_handler=AsyncMock(), write_handler=AsyncMock())
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=True)
        assert tool is not None
        schema = tool.parameters.model_json_schema()
        op_enum = schema["$defs"]["CreateInput"]["properties"]["operation"]["const"]
        assert op_enum == "create"

    def test_allow_mutations_false_drops_mutating_ops(self) -> None:
        cap = _cap_with_ops(read_handler=AsyncMock(), write_handler=AsyncMock())
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=False)
        assert tool is not None
        schema = tool.parameters.model_json_schema()
        # Only "list" operation should remain
        assert "CreateInput" not in schema.get("$defs", {})

    def test_allow_mutations_false_all_mutating_returns_none(self) -> None:
        cap = _cap_with_ops(write_handler=AsyncMock())
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=False)
        assert tool is None


class TestDispatch:
    @pytest.mark.anyio
    async def test_dispatches_to_correct_handler(self) -> None:
        handler = AsyncMock(return_value={"id": "stask-123", "name": "daily"})
        cap = _cap_with_ops(write_handler=handler)
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=True)
        assert tool is not None

        result = await tool.execute("tc-1", tool.parameters(operation="create", message="hello"))
        handler.assert_called_once()
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["id"] == "stask-123"


class TestErrorMapping:
    @pytest.mark.anyio
    async def test_not_found_maps_to_error(self) -> None:
        handler = AsyncMock(side_effect=ActionNotFound("gone"))
        cap = _cap_with_ops(read_handler=handler)
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=True)
        assert tool is not None
        result = await tool.execute("tc-1", tool.parameters(operation="list"))
        assert result.is_error
        assert "gone" in result.content[0].text

    @pytest.mark.anyio
    async def test_permission_denied_maps_to_error(self) -> None:
        handler = AsyncMock(side_effect=ActionPermissionDenied("not owner"))
        cap = _cap_with_ops(read_handler=handler)
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=True)
        assert tool is not None
        result = await tool.execute("tc-1", tool.parameters(operation="list"))
        assert result.is_error
        assert "not owner" in result.content[0].text

    @pytest.mark.anyio
    async def test_invalid_input_maps_to_error(self) -> None:
        handler = AsyncMock(side_effect=ActionInvalidInput("bad cron"))
        cap = _cap_with_ops(read_handler=handler)
        tool = build_capability_tool(cap, _fake_context_factory, allow_mutations=True)
        assert tool is not None
        result = await tool.execute("tc-1", tool.parameters(operation="list"))
        assert result.is_error
        assert "bad cron" in result.content[0].text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_agent_action_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.agents.actions.builder'`

- [ ] **Step 3: Implement `builder.py`**

```python
# backend/cubeplex/agents/actions/builder.py
"""Generic factory: AgentCapability → one cubepi AgentTool.

Builds a Pydantic discriminated-union input model from the capability's
operations, dispatches to the matching handler, and maps domain exceptions
to AgentToolResult(is_error=True).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any, Literal, Union, get_args

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
    AgentCapability,
    AgentOperation,
)

type ContextFactory = Callable[[], AbstractAsyncContextManager[tuple[ScopeContext, Any]]]


def _make_op_model(op: AgentOperation) -> type[BaseModel]:
    """Create a per-operation input model with a fixed `operation` literal."""
    fields: dict[str, Any] = {
        "operation": (Literal[op.name], Field(default=op.name)),  # type: ignore[valid-type]
    }
    for name, field_info in op.input_model.model_fields.items():
        fields[name] = (field_info.annotation, field_info)

    model = type(
        f"{op.name.capitalize()}Input",
        (BaseModel,),
        {"__annotations__": {k: v[0] for k, v in fields.items()}, **{k: v[1] for k, v in fields.items()}},
    )
    return model


def build_capability_tool(
    cap: AgentCapability,
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
) -> AgentTool[Any] | None:
    """Build one cubepi AgentTool for a capability, or None if no ops survive the gate."""
    ops = [op for op in cap.operations if allow_mutations or not op.mutates]
    if not ops:
        return None

    op_models = {op.name: (_make_op_model(op), op) for op in ops}

    if len(op_models) == 1:
        only_name = next(iter(op_models))
        union_model = op_models[only_name][0]
    else:
        union_type = Union[tuple(m for m, _ in op_models.values())]  # type: ignore[valid-type]
        discriminated = Annotated[union_type, Field(discriminator="operation")]

        class _CapInput(BaseModel):
            root: discriminated  # type: ignore[valid-type]

            def __init__(self, **data: Any) -> None:
                if "root" not in data and "operation" in data:
                    super().__init__(root=data)
                else:
                    super().__init__(**data)

            @property
            def operation(self) -> str:
                return self.root.operation  # type: ignore[attr-defined, no-any-return]

        _CapInput.__name__ = f"{cap.name.capitalize()}Input"
        _CapInput.__qualname__ = _CapInput.__name__
        union_model = _CapInput

    handler_map: dict[str, AgentOperation] = {op.name: op for op in ops}

    async def _execute(
        tool_call_id: str,
        args: Any,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        inner = args.root if hasattr(args, "root") else args
        op_name: str = inner.operation
        op = handler_map.get(op_name)
        if op is None:
            return AgentToolResult(
                content=[TextContent(text=f"Unknown operation: {op_name}")],
                is_error=True,
            )

        input_data = {
            k: v for k, v in inner.model_dump().items() if k != "operation"
        }
        parsed_input = op.input_model(**input_data) if input_data else op.input_model()

        try:
            async with context_factory() as (ctx, session):
                result = await op.handler(ctx, session, parsed_input)
        except ActionNotFound as exc:
            return AgentToolResult(
                content=[TextContent(text=f"NOT_FOUND: {exc}")], is_error=True
            )
        except ActionPermissionDenied as exc:
            return AgentToolResult(
                content=[TextContent(text=f"PERMISSION_DENIED: {exc}")], is_error=True
            )
        except ActionInvalidInput as exc:
            return AgentToolResult(
                content=[TextContent(text=f"INVALID_INPUT: {exc}")], is_error=True
            )

        text = json.dumps(result, default=str) if not isinstance(result, str) else result
        return AgentToolResult(content=[TextContent(text=text)])

    return AgentTool(
        name=cap.name,
        description=cap.description,
        parameters=union_model,
        execute=_execute,
    )
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_agent_action_builder.py -v`
Expected: all pass

- [ ] **Step 5: Run mypy**

Run: `cd backend && uv run mypy cubeplex/agents/actions/builder.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/agents/actions/builder.py backend/tests/unit/test_agent_action_builder.py
git commit -m "feat(actions): generic capability tool builder with mutation gate"
```

---

### Task 3: ScheduledTaskService — extract business logic from routes

**Files:**
- Create: `backend/cubeplex/services/scheduled_task.py`
- Create: `backend/tests/unit/test_scheduled_task_service.py`

This is the largest task. The service absorbs all logic from `ws_scheduled_tasks.py`: validation, `next_fire_at` computation, timezone normalization, owner-or-admin authorization, and the resume missed-run policy. The service owns the transaction (single commit per mutating call).

- [ ] **Step 1: Write service tests — create + authorization**

```python
# backend/tests/unit/test_scheduled_task_service.py
"""Unit tests for ScheduledTaskService."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import ActionInvalidInput, ActionNotFound, ActionPermissionDenied
from cubeplex.models.membership import Role
from cubeplex.models.scheduled_task import ScheduledTask
from cubeplex.services.scheduled_task import ScheduledTaskService

pytestmark = pytest.mark.e2e  # needs real DB


def _owner_ctx(
    org_id: str = "org-00000000000000",
    workspace_id: str = "ws-00000000000000",
    user_id: str = "usr-owner",
) -> ScopeContext:
    return ScopeContext(
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role=Role.MEMBER,
    )


def _admin_ctx(
    org_id: str = "org-00000000000000",
    workspace_id: str = "ws-00000000000000",
    user_id: str = "usr-admin",
) -> ScopeContext:
    return ScopeContext(
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role=Role.ADMIN,
    )


def _other_ctx(
    org_id: str = "org-00000000000000",
    workspace_id: str = "ws-00000000000000",
    user_id: str = "usr-other",
) -> ScopeContext:
    return ScopeContext(
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role=Role.MEMBER,
    )


async def _create_task(
    session: AsyncSession,
    ctx: ScopeContext | None = None,
    **overrides: object,
) -> ScheduledTask:
    svc = ScheduledTaskService()
    if ctx is None:
        ctx = _owner_ctx()
    defaults: dict[str, object] = {
        "name": "test-task",
        "prompt": "do something",
        "schedule_kind": "interval",
        "interval_seconds": 3600,
        "target_mode": "new_each_run",
    }
    defaults.update(overrides)
    return await svc.create(ctx, session, defaults)


class TestCreate:
    @pytest.mark.anyio
    async def test_create_interval(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session)
        assert task.status == "active"
        assert task.next_fire_at is not None
        assert task.owner_user_id == "usr-owner"

    @pytest.mark.anyio
    async def test_create_cron_requires_expr(self, async_session: AsyncSession) -> None:
        with pytest.raises(ActionInvalidInput, match="cron_expr"):
            await _create_task(async_session, schedule_kind="cron", interval_seconds=None)

    @pytest.mark.anyio
    async def test_create_once_requires_run_at(self, async_session: AsyncSession) -> None:
        with pytest.raises(ActionInvalidInput, match="run_at"):
            await _create_task(
                async_session,
                schedule_kind="once",
                interval_seconds=None,
            )


class TestAuthorization:
    @pytest.mark.anyio
    async def test_owner_can_pause(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session, ctx=_owner_ctx())
        svc = ScheduledTaskService()
        result = await svc.pause(_owner_ctx(), async_session, task.id)
        assert result.status == "paused"

    @pytest.mark.anyio
    async def test_admin_can_pause(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session, ctx=_owner_ctx())
        svc = ScheduledTaskService()
        result = await svc.pause(_admin_ctx(), async_session, task.id)
        assert result.status == "paused"

    @pytest.mark.anyio
    async def test_other_member_cannot_pause(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session, ctx=_owner_ctx())
        svc = ScheduledTaskService()
        with pytest.raises(ActionPermissionDenied):
            await svc.pause(_other_ctx(), async_session, task.id)


class TestPauseResume:
    @pytest.mark.anyio
    async def test_pause_resume_cycle(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session)
        svc = ScheduledTaskService()
        paused = await svc.pause(_owner_ctx(), async_session, task.id)
        assert paused.status == "paused"
        resumed = await svc.resume(_owner_ctx(), async_session, task.id)
        assert resumed.status == "active"
        assert resumed.next_fire_at is not None


class TestDelete:
    @pytest.mark.anyio
    async def test_delete_then_not_found(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session)
        svc = ScheduledTaskService()
        await svc.delete(_owner_ctx(), async_session, task.id)
        with pytest.raises(ActionNotFound):
            await svc.get_task(_owner_ctx(), async_session, task.id)


class TestUpdate:
    @pytest.mark.anyio
    async def test_update_prompt_no_schedule_slide(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session)
        svc = ScheduledTaskService()
        original_fire = task.next_fire_at
        updated = await svc.update(_owner_ctx(), async_session, task.id, {"prompt": "new"})
        assert updated.prompt == "new"
        assert updated.next_fire_at == original_fire

    @pytest.mark.anyio
    async def test_update_interval_recomputes_fire(self, async_session: AsyncSession) -> None:
        task = await _create_task(async_session)
        svc = ScheduledTaskService()
        original_fire = task.next_fire_at
        updated = await svc.update(
            _owner_ctx(), async_session, task.id, {"interval_seconds": 7200}
        )
        assert updated.next_fire_at != original_fire
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_scheduled_task_service.py -v --no-header 2>&1 | head -20`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.services.scheduled_task'`

- [ ] **Step 3: Implement `ScheduledTaskService`**

This service extracts all logic from `ws_scheduled_tasks.py`. The key rules:
- Service owns the transaction (single `session.commit()` at the end of each mutation).
- Does NOT use `ScopedRepository.add()` or `.delete()` (they auto-commit). Instead uses `session.add()` + explicit `session.commit()`.
- Uses `begin_nested()` for the race-safe history insert in `resume` (mirrors `_resume_next_fire`).
- Authorization: checks `task.owner_user_id == ctx.user_id or ctx.role == Role.ADMIN` before mutations.
- Validation: reuses `_validate_timezone`, `_validate_cron` from `cubeplex.api.schemas.ws_scheduled_tasks`.

```python
# backend/cubeplex/services/scheduled_task.py
"""ScheduledTaskService — source of truth for scheduled-task operations.

Both the REST route (thin adapter) and the agent tool (via the action
registry) delegate here. The service owns:
  - input validation (cron, timezone, schedule-kind consistency)
  - authorization (owner-or-admin for mutations)
  - schedule computation (next_fire_at via schedules.compute)
  - transaction ownership (single commit per mutating call)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import ActionInvalidInput, ActionNotFound, ActionPermissionDenied
from cubeplex.api.schemas.ws_scheduled_tasks import _validate_cron, _validate_timezone
from cubeplex.models.membership import Role
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.schedules.compute import as_utc, latest_due_before, next_fire_after


_SCHEDULE_FIELDS: frozenset[str] = frozenset(
    {"schedule_kind", "cron_expr", "interval_seconds", "run_at", "timezone"}
)


def _to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _initial_next_fire(task: ScheduledTask) -> datetime | None:
    now = datetime.now(UTC)
    if task.schedule_kind == "once":
        return task.run_at
    return next_fire_after(
        kind=task.schedule_kind,
        after=now,
        cron_expr=task.cron_expr,
        interval_seconds=task.interval_seconds,
        tz=task.timezone,
    )


def _validate_create(data: dict[str, Any]) -> None:
    """Validate create input — mirrors ScheduledTaskCreate model_validator."""
    kind = data.get("schedule_kind")
    tz = data.get("timezone", "UTC")
    try:
        _validate_timezone(tz)
    except ValueError as exc:
        raise ActionInvalidInput(str(exc)) from exc

    if kind == "cron":
        expr = data.get("cron_expr")
        if not expr:
            raise ActionInvalidInput("cron_expr required for cron schedule")
        try:
            _validate_cron(expr)
        except ValueError as exc:
            raise ActionInvalidInput(str(exc)) from exc
    elif kind == "interval":
        interval = data.get("interval_seconds")
        if not interval or interval < 60:
            raise ActionInvalidInput("interval_seconds >= 60 required for interval schedule")
    elif kind == "once":
        run_at = data.get("run_at")
        if run_at is None:
            raise ActionInvalidInput("run_at required for once schedule")
        if isinstance(run_at, datetime) and run_at.tzinfo is None:
            raise ActionInvalidInput("run_at must include a timezone offset")
    else:
        raise ActionInvalidInput(f"unknown schedule_kind: {kind!r}")

    end_at = data.get("end_at")
    if end_at is not None and isinstance(end_at, datetime) and end_at.tzinfo is None:
        raise ActionInvalidInput("end_at must include a timezone offset")

    target_mode = data.get("target_mode")
    if target_mode == "fixed" and not data.get("target_conversation_id"):
        raise ActionInvalidInput("target_conversation_id required when target_mode=fixed")


def _validate_patch(data: dict[str, Any]) -> None:
    """Validate patch input — mirrors ScheduledTaskPatch model_validator."""
    tz = data.get("timezone")
    if tz is not None:
        try:
            _validate_timezone(tz)
        except ValueError as exc:
            raise ActionInvalidInput(str(exc)) from exc

    expr = data.get("cron_expr")
    if expr is not None:
        try:
            _validate_cron(expr)
        except ValueError as exc:
            raise ActionInvalidInput(str(exc)) from exc

    run_at = data.get("run_at")
    if run_at is not None and isinstance(run_at, datetime) and run_at.tzinfo is None:
        raise ActionInvalidInput("run_at must include a timezone offset")
    end_at = data.get("end_at")
    if end_at is not None and isinstance(end_at, datetime) and end_at.tzinfo is None:
        raise ActionInvalidInput("end_at must include a timezone offset")

    kind = data.get("schedule_kind")
    if kind == "cron" and not data.get("cron_expr"):
        raise ActionInvalidInput("cron_expr required when changing schedule_kind to cron")
    if kind == "interval" and not data.get("interval_seconds"):
        raise ActionInvalidInput("interval_seconds required when changing schedule_kind to interval")
    if kind == "once" and data.get("run_at") is None:
        raise ActionInvalidInput("run_at required when changing schedule_kind to once")


class ScheduledTaskService:
    """Source of truth for scheduled-task CRUD + lifecycle.

    Stateless — no constructor args. All state comes from (ctx, session, input).
    """

    # --- reads ---

    async def list_tasks(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        _input: Any = None,
    ) -> list[ScheduledTask]:
        stmt = (
            select(ScheduledTask)
            .where(
                ScheduledTask.org_id == ctx.org_id,  # type: ignore[arg-type]
                ScheduledTask.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
                cast(Any, ScheduledTask.deleted_at).is_(None),
            )
            .order_by(ScheduledTask.created_at.desc())  # type: ignore[attr-defined]
            .limit(100)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_task(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        task = await self._load(ctx, session, task_id)
        return task

    async def list_runs(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> list[ScheduledTaskRun]:
        await self._load(ctx, session, task_id)
        stmt = (
            select(ScheduledTaskRun)
            .where(
                ScheduledTaskRun.org_id == ctx.org_id,  # type: ignore[arg-type]
                ScheduledTaskRun.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
                ScheduledTaskRun.scheduled_task_id == task_id,
            )
            .order_by(ScheduledTaskRun.scheduled_for.desc())  # type: ignore[attr-defined]
            .limit(50)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # --- mutations (service owns the transaction) ---

    async def create(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        data: dict[str, Any],
    ) -> ScheduledTask:
        _validate_create(data)

        if data.get("target_mode") == "fixed":
            await self._check_conversation_ownership(
                session, ctx, data.get("target_conversation_id", "")
            )

        run_at = data.get("run_at")
        end_at = data.get("end_at")
        task = ScheduledTask(
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            owner_user_id=ctx.user_id,
            name=data["name"],
            prompt=data["prompt"],
            schedule_kind=data["schedule_kind"],
            cron_expr=data.get("cron_expr"),
            interval_seconds=data.get("interval_seconds"),
            run_at=_to_utc_naive(run_at) if isinstance(run_at, datetime) else None,
            end_at=_to_utc_naive(end_at) if isinstance(end_at, datetime) else None,
            timezone=data.get("timezone", "UTC"),
            target_mode=data["target_mode"],
            target_conversation_id=data.get("target_conversation_id"),
            status="active",
        )
        task.next_fire_at = _initial_next_fire(task)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task

    async def update(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
        data: dict[str, Any],
    ) -> ScheduledTask:
        _validate_patch(data)
        task = await self._load_for_mutation(ctx, session, task_id)

        if data.get("target_mode") == "fixed" or (
            data.get("target_conversation_id") is not None and task.target_mode == "fixed"
        ):
            target = data.get("target_conversation_id") or task.target_conversation_id
            await self._check_conversation_ownership(
                session,
                ScopeContext(
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    user_id=task.owner_user_id,
                    role=ctx.role,
                ),
                target or "",
            )

        touched_schedule = False
        for field in (
            "name", "prompt", "schedule_kind", "cron_expr", "interval_seconds",
            "run_at", "timezone", "target_mode", "target_conversation_id",
        ):
            val = data.get(field)
            if val is None and field not in data:
                continue
            if field == "run_at" and isinstance(val, datetime):
                val = _to_utc_naive(val)
            if field in _SCHEDULE_FIELDS and val != getattr(task, field):
                touched_schedule = True
            setattr(task, field, val)

        if "end_at" in data:
            end_val = data["end_at"]
            task.end_at = _to_utc_naive(end_val) if isinstance(end_val, datetime) else None

        if touched_schedule:
            task.next_fire_at = _initial_next_fire(task) if task.status == "active" else None

        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return task

    async def pause(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        task = await self._load_for_mutation(ctx, session, task_id)
        task.status = "paused"
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return task

    async def resume(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        task = await self._load_for_mutation(ctx, session, task_id)
        task.status = "active"
        task.next_fire_at = await self._resume_next_fire(session, task)
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return task

    async def delete(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> None:
        task = await self._load_for_mutation(ctx, session, task_id)
        task.deleted_at = datetime.now(UTC)
        task.next_fire_at = None
        await session.commit()

    # --- internals ---

    async def _load(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        stmt = select(ScheduledTask).where(
            ScheduledTask.id == task_id,
            ScheduledTask.org_id == ctx.org_id,  # type: ignore[arg-type]
            ScheduledTask.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
            cast(Any, ScheduledTask.deleted_at).is_(None),
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        if task is None:
            raise ActionNotFound("Scheduled task not found")
        return task

    async def _load_for_mutation(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        task = await self._load(ctx, session, task_id)
        if task.owner_user_id != ctx.user_id and ctx.role != Role.ADMIN:
            raise ActionPermissionDenied("Owner or admin required")
        return task

    async def _check_conversation_ownership(
        self,
        session: AsyncSession,
        ctx: ScopeContext,
        conversation_id: str,
    ) -> None:
        from cubeplex.repositories.conversation import ConversationRepository

        conv_repo = ConversationRepository(
            session,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
        )
        if await conv_repo.get_by_id(conversation_id) is None:
            raise ActionInvalidInput("target_conversation_id must be your own conversation")

    async def _resume_next_fire(
        self,
        session: AsyncSession,
        task: ScheduledTask,
    ) -> datetime | None:
        now = datetime.now(UTC)
        anchor = as_utc(task.next_fire_at) if task.next_fire_at is not None else None

        if task.schedule_kind == "once":
            run_at = as_utc(task.run_at) if task.run_at is not None else None
            if run_at is not None and run_at <= now:
                scheduled_for_naive = _to_utc_naive(run_at)
                try:
                    async with session.begin_nested():
                        session.add(
                            ScheduledTaskRun(
                                scheduled_task_id=task.id,
                                org_id=task.org_id,
                                workspace_id=task.workspace_id,
                                scheduled_for=scheduled_for_naive,
                                claimed_at=now,
                                state="skipped_missed",
                                detail="paused past its one-shot fire time",
                            )
                        )
                        await session.flush()
                except IntegrityError:
                    pass
                return None
            return run_at

        if anchor is None or anchor > now:
            return anchor if anchor is not None else _initial_next_fire(task)

        latest_due = latest_due_before(
            kind=task.schedule_kind,
            candidate=anchor,
            now=now,
            cron_expr=task.cron_expr,
            interval_seconds=task.interval_seconds,
            tz=task.timezone,
        )
        session.add(
            ScheduledTaskRun(
                scheduled_task_id=task.id,
                org_id=task.org_id,
                workspace_id=task.workspace_id,
                scheduled_for=anchor,
                claimed_at=now,
                state="skipped_missed",
                detail=f"paused: skipped {anchor.isoformat()}..{latest_due.isoformat()}",
            )
        )
        return next_fire_after(
            kind=task.schedule_kind,
            after=latest_due,
            cron_expr=task.cron_expr,
            interval_seconds=task.interval_seconds,
            tz=task.timezone,
        )
```

- [ ] **Step 4: Make `_validate_cron` and `_validate_timezone` importable from schemas**

The functions are module-level in `ws_scheduled_tasks.py` (schemas). They are already importable — confirm by checking:

Run: `cd backend && uv run python -c "from cubeplex.api.schemas.ws_scheduled_tasks import _validate_cron, _validate_timezone; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Run the service tests**

Run: `cd backend && uv run pytest tests/unit/test_scheduled_task_service.py -v --no-header 2>&1 | tail -20`
Expected: all pass

- [ ] **Step 6: Run mypy on the service**

Run: `cd backend && uv run mypy cubeplex/services/scheduled_task.py`
Expected: `Success: no issues found`

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/services/scheduled_task.py backend/tests/unit/test_scheduled_task_service.py
git commit -m "feat(services): extract ScheduledTaskService from route handlers"
```

---

### Task 4: Refactor routes to thin adapters

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py`

Replace all inline business logic with calls to `ScheduledTaskService`. The route keeps `_to_out` serialization and the `_iso` helper (these are route-layer concerns — translating domain objects into HTTP response shapes). All existing E2E tests must stay green.

- [ ] **Step 1: Run existing route tests (baseline)**

Run: `cd backend && uv run pytest tests/e2e/test_scheduled_tasks_api.py -v --no-header 2>&1 | tail -25`
Expected: all pass (establish green baseline)

- [ ] **Step 2: Refactor the route module**

Replace `ws_scheduled_tasks.py` with a thin adapter. Keep:
- `router`, `_iso`, `_to_out`, `ScheduledTaskOut` import, all FastAPI decorators
- `ScopeContext.from_request(ctx)` construction per handler

Remove from the route module:
- `_initial_next_fire`, `_resume_next_fire`, `_to_utc_naive`, `_SCHEDULE_FIELDS`
- All inline validation, schedule computation, owner-or-admin checks
- Direct `ScheduledTask(...)` construction

Each handler becomes:

```python
# Pattern for all handlers:
from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import ActionInvalidInput, ActionNotFound, ActionPermissionDenied
from cubeplex.services.scheduled_task import ScheduledTaskService

_svc = ScheduledTaskService()


def _scope(ctx: RequestContext) -> ScopeContext:
    return ScopeContext(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        role=ctx.role,
    )


async def _handle_domain_errors(coro):
    """Call a service coroutine and translate domain exceptions to HTTP."""
    try:
        return await coro
    except ActionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ActionPermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ActionInvalidInput as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
```

Then each route is 3-5 lines:

```python
@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScheduledTaskOut)
async def create_task(
    body: ScheduledTaskCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        task = await _handle_domain_errors(
            _svc.create(_scope(ctx), session, body.model_dump())
        )
    return _to_out(task)
```

Apply this pattern to all 8 handlers: `create_task`, `list_tasks`, `get_task`, `patch_task`, `pause_task`, `resume_task`, `delete_task`, `list_task_runs`.

- [ ] **Step 3: Remove unused imports from the route module**

After refactoring, remove: `IntegrityError`, `ConversationRepository`, `next_fire_after`, `latest_due_before`, `as_utc`, and any other now-unused imports.

- [ ] **Step 4: Run the existing E2E tests**

Run: `cd backend && uv run pytest tests/e2e/test_scheduled_tasks_api.py -v --no-header 2>&1 | tail -25`
Expected: all pass (behavior-preserving refactor)

- [ ] **Step 5: Run mypy**

Run: `cd backend && uv run mypy cubeplex/api/routes/v1/ws_scheduled_tasks.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py
git commit -m "refactor(routes): scheduled-tasks routes → thin adapters over ScheduledTaskService"
```

---

### Task 5: Scheduled-tasks capability declaration

**Files:**
- Create: `backend/cubeplex/agents/actions/capabilities/scheduled_tasks.py`

Declares the 8 operations pointing at `ScheduledTaskService` methods, with LLM-facing descriptions.

- [ ] **Step 1: Create the capability declaration**

```python
# backend/cubeplex/agents/actions/capabilities/scheduled_tasks.py
"""scheduled_tasks capability — declares operations for the agent tool."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import AgentCapability, AgentOperation
from cubeplex.services.scheduled_task import ScheduledTaskService
from cubeplex.utils.time import utc_isoformat

_svc = ScheduledTaskService()


def _iso(dt: datetime | None) -> str | None:
    return utc_isoformat(dt) if dt is not None else None


def _task_summary(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "schedule_kind": task.schedule_kind,
        "cron_expr": task.cron_expr,
        "interval_seconds": task.interval_seconds,
        "timezone": task.timezone,
        "prompt": task.prompt,
        "target_mode": task.target_mode,
        "next_fire_at": _iso(task.next_fire_at),
        "last_fired_at": _iso(task.last_fired_at),
    }


# --- Input models per operation ---

class ListInput(BaseModel):
    pass


class GetInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID (e.g. stask-...)")


class ListRunsInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID")


class CreateInput(BaseModel):
    name: str = Field(description="Human-readable name for the task")
    prompt: str = Field(description="The prompt the agent will execute on each run")
    schedule_kind: Literal["cron", "interval", "once"] = Field(
        description="'cron' for cron expression, 'interval' for fixed seconds, 'once' for one-shot"
    )
    cron_expr: str | None = Field(
        default=None,
        description="5-field cron expression (required when schedule_kind='cron')",
    )
    interval_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Seconds between runs (required when schedule_kind='interval', min 60)",
    )
    run_at: datetime | None = Field(
        default=None,
        description="ISO 8601 datetime with tz offset (required when schedule_kind='once')",
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone for cron evaluation (e.g. 'Asia/Shanghai')",
    )
    target: Literal["new_each_run", "current_conversation"] = Field(
        default="new_each_run",
        description=(
            "'new_each_run' creates a fresh conversation each time (default). "
            "'current_conversation' pins results to the conversation you're in now."
        ),
    )
    end_at: datetime | None = Field(
        default=None,
        description="Optional: stop scheduling after this datetime (ISO 8601 with tz offset)",
    )


class UpdateInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID")
    name: str | None = None
    prompt: str | None = None
    schedule_kind: Literal["cron", "interval", "once"] | None = None
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str | None = None
    target_mode: Literal["fixed", "new_each_run"] | None = None
    target_conversation_id: str | None = None
    end_at: datetime | None = None


class PauseInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID")


class ResumeInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID")


class DeleteInput(BaseModel):
    task_id: str = Field(description="The scheduled task ID")


# --- Handlers (adapt service methods to the registry's calling convention) ---

async def _handle_list(ctx: ScopeContext, session: Any, _input: ListInput) -> Any:
    tasks = await _svc.list_tasks(ctx, session)
    return {"tasks": [_task_summary(t) for t in tasks]}


async def _handle_get(ctx: ScopeContext, session: Any, inp: GetInput) -> Any:
    task = await _svc.get_task(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_list_runs(ctx: ScopeContext, session: Any, inp: ListRunsInput) -> Any:
    runs = await _svc.list_runs(ctx, session, inp.task_id)
    return {
        "runs": [
            {
                "id": r.id,
                "scheduled_for": _iso(r.scheduled_for),
                "state": r.state,
                "run_id": r.run_id,
                "conversation_id": r.conversation_id,
                "detail": r.detail,
            }
            for r in runs
        ]
    }


async def _handle_create(ctx: ScopeContext, session: Any, inp: CreateInput) -> Any:
    data: dict[str, Any] = {
        "name": inp.name,
        "prompt": inp.prompt,
        "schedule_kind": inp.schedule_kind,
        "cron_expr": inp.cron_expr,
        "interval_seconds": inp.interval_seconds,
        "run_at": inp.run_at,
        "timezone": inp.timezone,
        "end_at": inp.end_at,
        "target_mode": "new_each_run",
        "target_conversation_id": None,
    }
    if inp.target == "current_conversation":
        if ctx.conversation_id is None:
            from cubeplex.agents.actions.types import ActionInvalidInput

            raise ActionInvalidInput("Cannot pin to current conversation: no conversation context")
        data["target_mode"] = "fixed"
        data["target_conversation_id"] = ctx.conversation_id

    task = await _svc.create(ctx, session, data)
    return _task_summary(task)


async def _handle_update(ctx: ScopeContext, session: Any, inp: UpdateInput) -> Any:
    data = {k: v for k, v in inp.model_dump(exclude={"task_id"}).items() if v is not None}
    if "end_at" in inp.model_fields_set:
        data["end_at"] = inp.end_at
    task = await _svc.update(ctx, session, inp.task_id, data)
    return _task_summary(task)


async def _handle_pause(ctx: ScopeContext, session: Any, inp: PauseInput) -> Any:
    task = await _svc.pause(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_resume(ctx: ScopeContext, session: Any, inp: ResumeInput) -> Any:
    task = await _svc.resume(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_delete(ctx: ScopeContext, session: Any, inp: DeleteInput) -> Any:
    await _svc.delete(ctx, session, inp.task_id)
    return {"deleted": True, "task_id": inp.task_id}


# --- Capability declaration ---

SCHEDULED_TASKS_CAPABILITY = AgentCapability(
    name="scheduled_tasks",
    description=(
        "Manage scheduled tasks in the current workspace. "
        "Each task runs a prompt on a cron, interval, or one-shot schedule. "
        "Only create, update, pause, resume, or delete tasks when the user "
        "has explicitly asked you to."
    ),
    operations=[
        AgentOperation(
            name="list",
            description="List all scheduled tasks in the workspace",
            input_model=ListInput,
            handler=_handle_list,
            mutates=False,
        ),
        AgentOperation(
            name="get",
            description="Get details of a specific scheduled task by ID",
            input_model=GetInput,
            handler=_handle_get,
            mutates=False,
        ),
        AgentOperation(
            name="list_runs",
            description="List run history for a scheduled task",
            input_model=ListRunsInput,
            handler=_handle_list_runs,
            mutates=False,
        ),
        AgentOperation(
            name="create",
            description=(
                "Create a new scheduled task. Only call when the user explicitly asks. "
                "Default target is 'new_each_run' (each trigger creates a new conversation). "
                "Use 'current_conversation' to pin results to this conversation."
            ),
            input_model=CreateInput,
            handler=_handle_create,
            mutates=True,
        ),
        AgentOperation(
            name="update",
            description="Update a scheduled task's settings. Only call when the user explicitly asks.",
            input_model=UpdateInput,
            handler=_handle_update,
            mutates=True,
        ),
        AgentOperation(
            name="pause",
            description="Pause a scheduled task. Only call when the user explicitly asks.",
            input_model=PauseInput,
            handler=_handle_pause,
            mutates=True,
        ),
        AgentOperation(
            name="resume",
            description="Resume a paused scheduled task. Only call when the user explicitly asks.",
            input_model=ResumeInput,
            handler=_handle_resume,
            mutates=True,
        ),
        AgentOperation(
            name="delete",
            description="Delete a scheduled task. Only call when the user explicitly asks.",
            input_model=DeleteInput,
            handler=_handle_delete,
            mutates=True,
        ),
    ],
)
```

- [ ] **Step 2: Verify the capability is importable**

Run: `cd backend && uv run python -c "from cubeplex.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY; print(len(SCHEDULED_TASKS_CAPABILITY.operations), 'operations')"`
Expected: `8 operations`

- [ ] **Step 3: Run mypy**

Run: `cd backend && uv run mypy cubeplex/agents/actions/capabilities/scheduled_tasks.py`
Expected: `Success: no issues found`

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/agents/actions/capabilities/
git commit -m "feat(actions): declare scheduled_tasks capability with 8 operations"
```

---

### Task 6: Registry + `tools_for_run`

**Files:**
- Create: `backend/cubeplex/agents/actions/registry.py`

Simple module: lists all capabilities, exposes `tools_for_run()` that the run manager calls.

- [ ] **Step 1: Create the registry**

```python
# backend/cubeplex/agents/actions/registry.py
"""Agent capability registry — the single entry point for run_manager."""

from __future__ import annotations

from typing import Any

from cubepi.agent.types import AgentTool

from cubeplex.agents.actions.builder import ContextFactory, build_capability_tool
from cubeplex.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubeplex.agents.actions.types import AgentCapability

AGENT_CAPABILITIES: list[AgentCapability] = [
    SCHEDULED_TASKS_CAPABILITY,
]


def tools_for_run(
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
) -> list[AgentTool[Any]]:
    """Build agent tools for all registered capabilities.

    Called once per run from run_manager. Returns one tool per capability
    (or zero if a capability has no surviving operations after the mutation gate).
    """
    tools: list[AgentTool[Any]] = []
    for cap in AGENT_CAPABILITIES:
        tool = build_capability_tool(cap, context_factory, allow_mutations=allow_mutations)
        if tool is not None:
            tools.append(tool)
    return tools
```

- [ ] **Step 2: Verify importable**

Run: `cd backend && uv run python -c "from cubeplex.agents.actions.registry import tools_for_run; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run mypy**

Run: `cd backend && uv run mypy cubeplex/agents/actions/registry.py`
Expected: `Success: no issues found`

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/agents/actions/registry.py
git commit -m "feat(actions): capability registry with tools_for_run entry point"
```

---

### Task 7: Thread trigger signal + wire `tools_for_run` into run_manager

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py:30` — add `trigger` to `RunContext`
- Modify: `backend/cubeplex/streams/run_manager.py:540` — thread `trigger` through `start_run` → `_execute_run` → `_run_cubepi_path`
- Modify: `backend/cubeplex/streams/run_manager.py:969` — accept `trigger`, wire `tools_for_run`
- Modify: `backend/cubeplex/schedules/dispatch.py:91` — pass `trigger="automated"` in `RunContext`

This task threads the interactivity signal and wires the action registry's tools into the agent.

- [ ] **Step 1: Add `trigger` field to `RunContext`**

In `run_manager.py`, change the `RunContext` dataclass (~line 30):

```python
@dataclass
class RunContext:
    """Scoped context required to execute a run."""

    user_id: str
    org_id: str
    workspace_id: str
    trigger: str = "interactive"  # "interactive" | "automated"
```

- [ ] **Step 2: Thread `trigger` from `_execute_run` to `_run_cubepi_path`**

Add `trigger` to `_run_cubepi_path`'s signature (~line 969) and pass it from `_execute_run` (~line 2102):

In `_run_cubepi_path` signature, add after `catalog_session`:
```python
    trigger: str = "interactive",
```

In the `_execute_run` call to `_run_cubepi_path` (~line 2102), add:
```python
    trigger=ctx.trigger,
```

- [ ] **Step 3: Wire `tools_for_run` into `_run_cubepi_path`**

After the existing `show_widget` tool block (after ~line 1220), add:

```python
        # Platform action tools (scheduled_tasks, etc.) — via the capability
        # registry. Automated runs get read-only tools (mutation gate).
        try:
            from cubeplex.agents.actions.registry import tools_for_run as _action_tools_for_run

            async with async_session_maker() as _action_session:
                _role = await MembershipRepository(_action_session).get_role(
                    user_id=ctx.user_id, workspace_id=ctx.workspace_id,
                )

            if _role is not None:
                from collections.abc import AsyncIterator as _ActionsAsyncIterator
                from contextlib import asynccontextmanager as _actions_acm

                from cubeplex.agents.actions.context import ScopeContext as _ScopeContext

                @_actions_acm
                async def _action_context_factory() -> _ActionsAsyncIterator[tuple[_ScopeContext, Any]]:
                    async with async_session_maker() as _sess:
                        yield (
                            _ScopeContext(
                                org_id=ctx.org_id,
                                workspace_id=ctx.workspace_id,
                                user_id=ctx.user_id,
                                role=_role,
                                conversation_id=conversation_id,
                            ),
                            _sess,
                        )

                _builtin_tools.extend(
                    _action_tools_for_run(
                        _action_context_factory,
                        allow_mutations=(trigger == "interactive"),
                    )
                )
        except Exception as _exc:
            logger.warning("platform action tools unavailable for cubepi run: {}", _exc)
```

- [ ] **Step 4: Update `dispatch_scheduled_run` to pass `trigger="automated"`**

In `dispatch.py` (~line 91), change:

```python
    ctx = RunContext(
        user_id=task.owner_user_id,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
        trigger="automated",
    )
```

- [ ] **Step 5: Run mypy on changed files**

Run: `cd backend && uv run mypy cubeplex/streams/run_manager.py cubeplex/schedules/dispatch.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Run the existing scheduled-task E2E tests (regression check)**

Run: `cd backend && uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py -v --no-header 2>&1 | tail -30`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/cubeplex/schedules/dispatch.py
git commit -m "feat(actions): thread trigger signal + wire tools_for_run into run_manager"
```

---

### Task 8: Full mypy + existing test suite guard

**Files:** none (verification only)

- [ ] **Step 1: Run full mypy**

Run: `cd backend && uv run mypy cubeplex/`
Expected: `Success: no issues found`

- [ ] **Step 2: Run all existing scheduled-task tests**

Run: `cd backend && uv run pytest tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py tests/unit/test_schedule_compute.py tests/unit/test_scheduled_task_schemas.py -v --no-header 2>&1 | tail -30`
Expected: all pass

- [ ] **Step 3: Run the new tests**

Run: `cd backend && uv run pytest tests/unit/test_agent_action_builder.py tests/unit/test_scheduled_task_service.py -v --no-header 2>&1 | tail -30`
Expected: all pass

- [ ] **Step 4: Commit (only if any fixup was needed)**

```bash
git add -A
git commit -m "fix: address mypy/test issues from integration"
```

---

## Self-Review Checklist

**Spec coverage:** All 8 operations (list, get, list_runs, create, update, pause, resume, delete) are declared in the capability (Task 5). The mutation gate (codex review HIGH #1) is implemented in the builder (Task 2) and wired via the trigger signal (Task 7). Transaction ownership (codex review HIGH #2) is in the service (Task 3). Route refactor to thin adapter (Task 4). Registry + tools_for_run (Task 6). Full-suite guard (Task 8).

**Placeholder scan:** No TBD, TODO, "fill in later", or "similar to Task N" found.

**Type consistency:** `ScopeContext` used consistently across context.py, builder.py, service, capability, and run_manager wiring. `AgentOperation`/`AgentCapability` used consistently across types.py, builder.py, and registry.py. `ScheduledTaskService` method signatures match between service (Task 3), route adapter (Task 4), and capability handlers (Task 5). `ContextFactory` type alias defined in builder.py, used in registry.py and run_manager wiring.
