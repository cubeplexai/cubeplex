"""create_scheduled_task tool — cubepi.AgentTool with auto IM-origin detection.

Factory: ``make_create_scheduled_task_tool(...)`` returns one
``cubepi.AgentTool``. Destination derivation is shared with the
``scheduled_tasks_create`` capability via
``cubebox.services.schedule_destination`` so both paths agree:

Default derivation (when caller omits ``target_mode``):
- IM-bound conversation → ``im_channel`` (survives ``/new``)
- Otherwise → ``fixed`` + current conversation

When the caller passes ``target_mode='new_each_run'`` without ``topic_id``
and the current conversation belongs to a topic, the tool inherits the
topic id so "create a schedule in a new conversation" inside a topic stays
inside that topic.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput
from cubebox.db.engine import async_session_maker
from cubebox.models import Role
from cubebox.repositories import MembershipRepository
from cubebox.services.schedule_destination import (
    Intent,
    derive_schedule_destination_for_conversation,
)
from cubebox.services.schedule_target_spec import ScheduleTargetError, ScheduleTargetSpec
from cubebox.services.scheduled_task import ScheduledTaskService


class CreateScheduledTaskArgs(BaseModel):
    """Input schema for create_scheduled_task."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Short human-readable title shown in the schedule list.",
    )
    prompt: str = Field(
        min_length=1,
        description="The prompt to run each time the schedule fires.",
    )
    schedule_kind: Literal["cron", "interval", "once"] = Field(
        description=(
            "'cron' for cron expressions, 'interval' for fixed seconds between fires, "
            "'once' for a single fire at run_at."
        ),
    )
    cron_expr: str | None = Field(
        default=None,
        description=(
            "Five-field cron expression (minute hour day month weekday). "
            "Required when schedule_kind='cron'."
        ),
    )
    interval_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Seconds between fires (>=60). Required when schedule_kind='interval'.",
    )
    run_at: str | None = Field(
        default=None,
        description=(
            "ISO 8601 timestamp with timezone offset (e.g. '2026-07-01T09:00:00+08:00'). "
            "Required when schedule_kind='once'."
        ),
    )
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA timezone the schedule is interpreted in (e.g. 'Asia/Shanghai'). Defaults to UTC."
        ),
    )
    target_mode: Literal["fixed", "new_each_run", "im_channel"] | None = Field(
        default=None,
        description=(
            "How the schedule's runs are routed. Leave None to auto-derive: "
            "IM-bound conversation → 'im_channel'; otherwise → 'fixed' (current conversation)."
        ),
    )
    target_conversation_id: str | None = Field(
        default=None,
        description=(
            "Existing conversation to fire into when target_mode='fixed'. Auto-filled when omitted."
        ),
    )
    topic_id: str | None = Field(
        default=None,
        description=(
            "Optional topic id for target_mode='new_each_run'. "
            "Inherited from the current conversation's topic when omitted."
        ),
    )


def _parse_run_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"run_at must be an ISO 8601 timestamp with timezone offset, got {value!r}"
        ) from exc
    if dt.tzinfo is None:
        raise ValueError("run_at must include a timezone offset")
    return dt


def make_create_scheduled_task_tool(
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    conversation_id: str,
) -> AgentTool[CreateScheduledTaskArgs]:
    """Build the create_scheduled_task cubepi.AgentTool bound to a run.

    A fresh DB session is opened per call. org_id / workspace_id /
    user_id / conversation_id are bound at construction (run-scoped).
    """

    svc = ScheduledTaskService()

    async def _execute(
        tool_call_id: str,
        args: CreateScheduledTaskArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        async with async_session_maker() as session:
            intent: Intent = args.target_mode if args.target_mode is not None else "auto"
            try:
                dest = await derive_schedule_destination_for_conversation(
                    session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    target_conversation_id=args.target_conversation_id,
                    explicit_topic_id=args.topic_id,
                )
            except ValueError as exc:
                return _error(str(exc))

            try:
                ScheduleTargetSpec(
                    target_mode=dest.target_mode,
                    target_conversation_id=dest.target_conversation_id,
                    topic_id=dest.topic_id,
                    im_account_id=dest.im_account_id,
                    im_channel_id=dest.im_channel_id,
                    im_scope_key=dest.im_scope_key,
                    im_scope_kind=dest.im_scope_kind,
                ).validate()
            except ScheduleTargetError as exc:
                return _error(str(exc))

            try:
                run_at_dt = _parse_run_at(args.run_at)
            except ValueError as exc:
                return _error(str(exc))

            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user_id, workspace_id=workspace_id)
            ctx = ScopeContext(
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                role=role or Role.MEMBER,
                conversation_id=conversation_id,
            )

            data: dict[str, Any] = {
                "name": args.name,
                "prompt": args.prompt,
                "schedule_kind": args.schedule_kind,
                "cron_expr": args.cron_expr,
                "interval_seconds": args.interval_seconds,
                "run_at": run_at_dt,
                "timezone": args.timezone or "UTC",
                **dest.as_create_fields(),
            }

            try:
                task = await svc.create(ctx, session, data)
            except ActionInvalidInput as exc:
                return _error(str(exc))

            result = {
                "status": "created",
                "id": task.id,
                "name": task.name,
                "target_mode": task.target_mode,
                "schedule_kind": task.schedule_kind,
                "next_fire_at": (
                    task.next_fire_at.isoformat() if task.next_fire_at is not None else None
                ),
            }
            return AgentToolResult(content=[TextContent(text=json.dumps(result))])

    return AgentTool(
        name="create_scheduled_task",
        description=(
            "Create a scheduled task that runs `prompt` on a cron / interval / once schedule. "
            "When called inside an IM conversation, the schedule defaults to posting back to "
            "that IM channel (surviving `/new`). When called outside IM, the schedule defaults "
            "to running in the current conversation. Pass `target_mode='new_each_run'` to start "
            "a fresh conversation each run (optionally under a topic — the current topic is "
            "inherited automatically)."
        ),
        parameters=CreateScheduledTaskArgs,
        execute=_execute,
    )


def _error(message: str) -> AgentToolResult:
    payload = {"status": "error", "error": message}
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload))],
        is_error=True,
    )
