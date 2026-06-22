"""create_scheduled_task tool — cubepi.AgentTool with auto IM-origin detection.

Factory: ``make_create_scheduled_task_tool(...)`` returns one
``cubepi.AgentTool``. The tool detects whether the current conversation is
bound to an IM thread (via ``IMThreadLink``) and derives sensible defaults
so the agent can say "remind me every morning" without explicitly choosing
between fixed / new_each_run / im_channel destinations.

Default derivation (when caller omits ``target_mode``):
- IM origin (link exists)  → ``im_channel`` + im_* fields from the link
- Web/API (no link)        → ``fixed`` + ``target_conversation_id`` = current conv

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
from sqlalchemy import select

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput
from cubebox.db.engine import async_session_maker
from cubebox.models import Role
from cubebox.models.conversation import Conversation
from cubebox.models.im_connector import IMThreadLink
from cubebox.repositories import MembershipRepository
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
        description="Five-field cron expression (minute hour day month weekday). Required when schedule_kind='cron'.",
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
        description="IANA timezone the schedule is interpreted in (e.g. 'Asia/Shanghai'). Defaults to UTC.",
    )
    target_mode: Literal["fixed", "new_each_run", "im_channel"] | None = Field(
        default=None,
        description=(
            "How the schedule's runs are routed. Leave None to auto-derive: "
            "IM origin → 'im_channel'; otherwise → 'fixed' (current conversation)."
        ),
    )
    target_conversation_id: str | None = Field(
        default=None,
        description="Existing conversation to fire into when target_mode='fixed'. Auto-filled when omitted.",
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
            link_stmt = select(IMThreadLink).where(
                IMThreadLink.conversation_id == conversation_id,  # type: ignore[arg-type]
                IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
            link = (await session.execute(link_stmt)).scalar_one_or_none()

            target_mode = args.target_mode
            target_conversation_id = args.target_conversation_id
            topic_id = args.topic_id
            im_account_id: str | None = None
            im_channel_id: str | None = None
            im_scope_key: str | None = None
            im_scope_kind: str | None = None

            if target_mode is None:
                if link is not None:
                    target_mode = "im_channel"
                    im_account_id = link.account_id
                    im_channel_id = link.channel_id
                    im_scope_key = link.scope_key
                    im_scope_kind = link.scope_kind
                else:
                    target_mode = "fixed"
                    if target_conversation_id is None:
                        target_conversation_id = conversation_id
            elif target_mode == "im_channel":
                if link is None:
                    return _error(
                        "im_channel target requires this conversation to be bound "
                        "to an IM channel; no IMThreadLink found for the current conversation."
                    )
                im_account_id = link.account_id
                im_channel_id = link.channel_id
                im_scope_key = link.scope_key
                im_scope_kind = link.scope_kind
            elif target_mode == "fixed":
                if target_conversation_id is None:
                    target_conversation_id = conversation_id

            if target_mode == "new_each_run" and topic_id is None:
                current_conv = await session.get(Conversation, conversation_id)
                if current_conv is not None and current_conv.topic_id is not None:
                    topic_id = current_conv.topic_id

            try:
                ScheduleTargetSpec(
                    target_mode=target_mode,
                    target_conversation_id=target_conversation_id,
                    topic_id=topic_id,
                    im_account_id=im_account_id,
                    im_channel_id=im_channel_id,
                    im_scope_key=im_scope_key,
                    im_scope_kind=im_scope_kind,
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
                "target_mode": target_mode,
                "target_conversation_id": target_conversation_id,
                "topic_id": topic_id,
                "im_account_id": im_account_id,
                "im_channel_id": im_channel_id,
                "im_scope_key": im_scope_key,
                "im_scope_kind": im_scope_kind,
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
