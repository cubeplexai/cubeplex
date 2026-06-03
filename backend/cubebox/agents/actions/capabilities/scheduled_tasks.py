"""Scheduled-tasks agent capability — 8 operations for CRUD and lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput, AgentCapability, AgentOperation
from cubebox.services.scheduled_task import ScheduledTaskService
from cubebox.utils.time import utc_isoformat

_svc = ScheduledTaskService()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class ListInput(BaseModel):
    """No parameters — returns all non-deleted tasks in the workspace."""


class GetInput(BaseModel):
    task_id: str


class ListRunsInput(BaseModel):
    task_id: str


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
    CronSchedule | IntervalSchedule | OnceSchedule,
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


class PauseInput(BaseModel):
    task_id: str


class ResumeInput(BaseModel):
    task_id: str


class DeleteInput(BaseModel):
    task_id: str


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_list(ctx: ScopeContext, session: AsyncSession, inp: ListInput) -> Any:
    tasks = await _svc.list_tasks(ctx, session)
    return [_task_summary(t) for t in tasks]


async def _handle_get(ctx: ScopeContext, session: AsyncSession, inp: GetInput) -> Any:
    task = await _svc.get_task(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_list_runs(ctx: ScopeContext, session: AsyncSession, inp: ListRunsInput) -> Any:
    runs = await _svc.list_runs(ctx, session, inp.task_id)
    return [
        {
            "id": r.id,
            "scheduled_for": _iso(r.scheduled_for),
            "claimed_at": _iso(r.claimed_at),
            "started_at": _iso(r.started_at),
            "state": r.state,
            "conversation_id": r.conversation_id,
            "detail": r.detail,
        }
        for r in runs
    ]


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
    match sched:
        case CronSchedule():
            cron_expr: str | None = sched.cron_expr
            interval_seconds: int | None = None
            run_at: datetime | None = None
            timezone: str = sched.timezone
        case IntervalSchedule():
            cron_expr = None
            interval_seconds = sched.interval_seconds
            run_at = None
            timezone = "UTC"
        case OnceSchedule():
            cron_expr = None
            interval_seconds = None
            run_at = sched.run_at
            timezone = "UTC"
    data: dict[str, Any] = {
        "name": inp.name,
        "prompt": inp.prompt,
        "schedule_kind": sched.kind,
        "cron_expr": cron_expr,
        "interval_seconds": interval_seconds,
        "run_at": run_at,
        "timezone": timezone,
        "target_mode": target_mode,
        "target_conversation_id": target_conversation_id,
        "end_at": inp.end_at,
    }
    task = await _svc.create(ctx, session, data)
    return _task_summary(task)


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
        match sched:
            case CronSchedule():
                data["cron_expr"] = sched.cron_expr
                data["timezone"] = sched.timezone
            case IntervalSchedule():
                data["interval_seconds"] = sched.interval_seconds
            case OnceSchedule():
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


async def _handle_pause(ctx: ScopeContext, session: AsyncSession, inp: PauseInput) -> Any:
    task = await _svc.pause(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_resume(ctx: ScopeContext, session: AsyncSession, inp: ResumeInput) -> Any:
    task = await _svc.resume(ctx, session, inp.task_id)
    return _task_summary(task)


async def _handle_delete(ctx: ScopeContext, session: AsyncSession, inp: DeleteInput) -> Any:
    await _svc.delete(ctx, session, inp.task_id)
    return {"deleted": True, "task_id": inp.task_id}


# ---------------------------------------------------------------------------
# Capability declaration
# ---------------------------------------------------------------------------

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
