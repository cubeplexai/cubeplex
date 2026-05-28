"""Workspace scheduled-task routes. Scope-isolated: no admin/cross-ws variant.

Reads require membership; mutations (edit/pause/resume/delete) require being the
task owner OR a workspace admin — a scheduled run executes as the owner, so a
non-owner editing the prompt would run code under the owner's identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.ws_scheduled_tasks import (
    ScheduledTaskCreate,
    ScheduledTaskListOut,
    ScheduledTaskOut,
    ScheduledTaskPatch,
    ScheduledTaskRunOut,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db.engine import async_session_maker
from cubebox.models import Role
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.repositories.conversation import ConversationRepository
from cubebox.repositories.scheduled_task import (
    ScheduledTaskRepository,
    ScheduledTaskRunRepository,
)
from cubebox.schedules.compute import as_utc, latest_due_before, next_fire_after
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/scheduled-tasks", tags=["scheduled-tasks"])


def _iso(dt: datetime | None) -> str | None:
    return utc_isoformat(dt) if dt is not None else None


def _to_utc_naive(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC then strip tzinfo for storage.

    Scheduled-task DB columns are ``timestamp without time zone``; storing an
    aware datetime drops the offset and persists the wall-clock value, so a
    client-supplied ``2030-01-01T09:00:00-05:00`` would otherwise come back
    as 09:00Z instead of 14:00Z (firing five hours early). Normalizing to
    UTC naive before persist keeps every column in the same frame as the
    rest of the module's arithmetic (which treats naive DB values as UTC).
    """
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


# Fields whose change requires recomputing next_fire_at. Editing only
# name/prompt/target_* must NOT slide the schedule (codex P2): on an hourly
# task due at 13:00, a 12:30 prompt edit shouldn't push the next fire to 13:30.
_SCHEDULE_FIELDS: frozenset[str] = frozenset(
    {"schedule_kind", "cron_expr", "interval_seconds", "run_at", "timezone"}
)


def _to_out(t: ScheduledTask) -> ScheduledTaskOut:
    return ScheduledTaskOut(
        id=t.id,
        name=t.name,
        status=t.status,
        schedule_kind=t.schedule_kind,
        cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds,
        run_at=_iso(t.run_at),
        timezone=t.timezone,
        prompt=t.prompt,
        target_mode=t.target_mode,
        target_conversation_id=t.target_conversation_id,
        owner_user_id=t.owner_user_id,
        next_fire_at=_iso(t.next_fire_at),
        last_fired_at=_iso(t.last_fired_at),
        created_at=utc_isoformat(t.created_at),
        updated_at=utc_isoformat(t.updated_at),
    )


def _initial_next_fire(t: ScheduledTask) -> datetime | None:
    now = datetime.now(UTC)
    if t.schedule_kind == "once":
        return t.run_at
    return next_fire_after(
        kind=t.schedule_kind,
        after=now,
        cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds,
        tz=t.timezone,
    )


async def _resume_next_fire(session: AsyncSession, t: ScheduledTask) -> datetime | None:
    """Resume policy: account for occurrences that fell due while paused.

    Mirrors the outage missed-run policy: at most ONE summary history row.
    """
    now = datetime.now(UTC)
    anchor = as_utc(t.next_fire_at) if t.next_fire_at is not None else None
    if t.schedule_kind == "once":
        run_at = as_utc(t.run_at) if t.run_at is not None else None
        if run_at is not None and run_at <= now:
            # Idempotency: a prior fire OR a prior resume of the same expired
            # one-shot may have already recorded a row at scheduled_for=run_at.
            # The unique (scheduled_task_id, scheduled_for) constraint would
            # otherwise 500 the resume request on a repeat pause/resume cycle.
            scheduled_for_naive = _to_utc_naive(run_at)
            existing = (
                await session.execute(
                    select(cast(Any, ScheduledTaskRun.id)).where(
                        cast(Any, ScheduledTaskRun.scheduled_task_id) == t.id,
                        cast(Any, ScheduledTaskRun.scheduled_for) == scheduled_for_naive,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    ScheduledTaskRun(
                        scheduled_task_id=t.id,
                        org_id=t.org_id,
                        workspace_id=t.workspace_id,
                        scheduled_for=scheduled_for_naive,
                        claimed_at=now,
                        state="skipped_missed",
                        detail="paused past its one-shot fire time",
                    )
                )
            return None
        return run_at
    if anchor is None or anchor > now:
        return anchor if anchor is not None else _initial_next_fire(t)
    latest_due = latest_due_before(
        kind=t.schedule_kind,
        candidate=anchor,
        now=now,
        cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds,
        tz=t.timezone,
    )
    session.add(
        ScheduledTaskRun(
            scheduled_task_id=t.id,
            org_id=t.org_id,
            workspace_id=t.workspace_id,
            scheduled_for=anchor,
            claimed_at=now,
            state="skipped_missed",
            detail=f"paused: skipped {anchor.isoformat()}..{latest_due.isoformat()}",
        )
    )
    return next_fire_after(
        kind=t.schedule_kind,
        after=latest_due,
        cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds,
        tz=t.timezone,
    )


async def _load_for_mutation(ctx: RequestContext, task_id: str) -> ScheduledTask:
    """Load task (404 if missing) and enforce owner-or-admin (403 otherwise)."""
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = await repo.get_active(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
    if task.owner_user_id != ctx.user.id and ctx.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner or admin required")
    return task


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScheduledTaskOut)
async def create_task(
    body: ScheduledTaskCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        if body.target_mode == "fixed":
            conv_repo = ConversationRepository(
                session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user.id,
            )
            if await conv_repo.get_by_id(body.target_conversation_id or "") is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "target_conversation_id must be your own conversation",
                )
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = ScheduledTask(
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            owner_user_id=ctx.user.id,
            name=body.name,
            prompt=body.prompt,
            schedule_kind=body.schedule_kind,
            cron_expr=body.cron_expr,
            interval_seconds=body.interval_seconds,
            run_at=_to_utc_naive(body.run_at) if body.run_at is not None else None,
            timezone=body.timezone,
            target_mode=body.target_mode,
            target_conversation_id=body.target_conversation_id,
            status="active",
        )
        task.next_fire_at = _initial_next_fire(task)
        task = await repo.create(task)
        return _to_out(task)


@router.get("", response_model=ScheduledTaskListOut)
async def list_tasks(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskListOut:
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        tasks = await repo.list_all()
    return ScheduledTaskListOut(tasks=[_to_out(t) for t in tasks])


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = await repo.get_active(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
    return _to_out(task)


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def patch_task(
    task_id: str,
    body: ScheduledTaskPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = await repo.get_active(task_id)
        assert task is not None
        if body.target_mode == "fixed" or (
            body.target_conversation_id is not None and task.target_mode == "fixed"
        ):
            conv_repo = ConversationRepository(
                session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=task.owner_user_id,
            )
            target = body.target_conversation_id or task.target_conversation_id
            if target is None or await conv_repo.get_by_id(target) is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "target_conversation_id must be the owner's conversation",
                )
        touched_schedule = False
        for field in (
            "name",
            "prompt",
            "schedule_kind",
            "cron_expr",
            "interval_seconds",
            "run_at",
            "timezone",
            "target_mode",
            "target_conversation_id",
        ):
            val = getattr(body, field)
            if val is None:
                continue
            if field == "run_at":
                val = _to_utc_naive(val)
            # Only flip touched_schedule when a schedule field's value
            # *actually changes*. The UI's edit dialog re-sends the whole
            # form (including unchanged schedule_kind / interval_seconds /
            # run_at / timezone), so presence in the patch alone would
            # slide next_fire_at on every name/prompt edit. Compare against
            # the current task value (already normalized in storage form).
            if field in _SCHEDULE_FIELDS and val != getattr(task, field):
                touched_schedule = True
            setattr(task, field, val)
        # Only recompute next_fire_at when the schedule actually changed.
        # Metadata-only edits (name/prompt/target_*) must NOT slide the next
        # fire forward; that would silently delay or skip the pending run.
        if touched_schedule:
            task.next_fire_at = _initial_next_fire(task) if task.status == "active" else None
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.post("/{task_id}/pause", response_model=ScheduledTaskOut)
async def pause_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = await repo.get_active(task_id)
        assert task is not None
        task.status = "paused"
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.post("/{task_id}/resume", response_model=ScheduledTaskOut)
async def resume_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        task = await repo.get_active(task_id)
        assert task is not None
        task.status = "active"
        task.next_fire_at = await _resume_next_fire(session, task)
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        await repo.soft_delete(task_id)


@router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunOut])
async def list_task_runs(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> list[ScheduledTaskRunOut]:
    async with async_session_maker() as session:
        task_repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        if await task_repo.get_active(task_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
        run_repo = ScheduledTaskRunRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        rows = await run_repo.list_for_task(task_id)
    return [
        ScheduledTaskRunOut(
            id=r.id,
            scheduled_for=utc_isoformat(r.scheduled_for),
            claimed_at=utc_isoformat(r.claimed_at),
            started_at=_iso(r.started_at),
            state=r.state,
            retry_count=r.retry_count,
            next_retry_at=_iso(r.next_retry_at),
            run_id=r.run_id,
            conversation_id=r.conversation_id,
            detail=r.detail,
        )
        for r in rows
    ]
