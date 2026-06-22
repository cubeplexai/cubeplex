"""Workspace scheduled-task routes. Scope-isolated: no admin/cross-ws variant.

Reads require membership; mutations (edit/pause/resume/delete) require being the
task owner OR a workspace admin — a scheduled run executes as the owner, so a
non-owner editing the prompt would run code under the owner's identity.

Route handlers are thin adapters: construct ScopeContext, call the service,
map domain exceptions to HTTP responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
)
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
from cubebox.models.scheduled_task import ScheduledTask
from cubebox.services.scheduled_task import ScheduledTaskService
from cubebox.utils.time import utc_isoformat

router = APIRouter(
    prefix="/ws/{workspace_id}/scheduled-tasks",
    tags=["scheduled-tasks"],
)

_svc = ScheduledTaskService()


def _scope(ctx: RequestContext) -> ScopeContext:
    return ScopeContext(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        role=ctx.role,
    )


def _iso(dt: datetime | None) -> str | None:
    return utc_isoformat(dt) if dt is not None else None


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
        topic_id=t.topic_id,
        im_account_id=t.im_account_id,
        im_channel_id=t.im_channel_id,
        im_scope_key=t.im_scope_key,
        im_scope_kind=t.im_scope_kind,
        owner_user_id=t.owner_user_id,
        next_fire_at=_iso(t.next_fire_at),
        last_fired_at=_iso(t.last_fired_at),
        end_at=_iso(t.end_at),
        created_at=utc_isoformat(t.created_at),
        updated_at=utc_isoformat(t.updated_at),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScheduledTaskOut)
async def create_task(
    body: ScheduledTaskCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        try:
            task = await _svc.create(_scope(ctx), session, body.model_dump())
        except ActionInvalidInput as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _to_out(task)


@router.get("", response_model=ScheduledTaskListOut)
async def list_tasks(
    ctx: Annotated[RequestContext, Depends(require_member)],
    topic_id: Annotated[str | None, Query()] = None,
    im_account_id: Annotated[str | None, Query()] = None,
    im_channel_id: Annotated[str | None, Query()] = None,
) -> ScheduledTaskListOut:
    async with async_session_maker() as session:
        tasks = await _svc.list_tasks(
            _scope(ctx),
            session,
            topic_id=topic_id,
            im_account_id=im_account_id,
            im_channel_id=im_channel_id,
        )
    return ScheduledTaskListOut(tasks=[_to_out(t) for t in tasks])


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        try:
            task = await _svc.get_task(_scope(ctx), session, task_id)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_out(task)


_PATCH_MODE_LOCKED_FIELDS: frozenset[str] = frozenset(
    {
        "target_mode",
        "target_conversation_id",
        "im_account_id",
        "im_channel_id",
        "im_scope_key",
        "im_scope_kind",
    }
)


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def patch_task(
    task_id: str,
    body: ScheduledTaskPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    # Mode-bound destination fields are immutable post-create. We gate on
    # `model_fields_set` membership so an explicit `null` is rejected just
    # like a non-null value would be — the user's intent is to change the
    # destination shape, which only delete-and-recreate supports.
    locked = _PATCH_MODE_LOCKED_FIELDS & body.model_fields_set
    if locked:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "target_mode / target_conversation_id / im_* cannot be changed via PATCH; "
                "delete and recreate the schedule"
            ),
        )
    async with async_session_maker() as session:
        try:
            data = body.model_dump(exclude_unset=True)
            task = await _svc.update(_scope(ctx), session, task_id, data)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ActionPermissionDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ActionInvalidInput as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _to_out(task)


@router.post("/{task_id}/pause", response_model=ScheduledTaskOut)
async def pause_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        try:
            task = await _svc.pause(_scope(ctx), session, task_id)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ActionPermissionDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _to_out(task)


@router.post("/{task_id}/resume", response_model=ScheduledTaskOut)
async def resume_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        try:
            task = await _svc.resume(_scope(ctx), session, task_id)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ActionPermissionDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _to_out(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    async with async_session_maker() as session:
        try:
            await _svc.delete(_scope(ctx), session, task_id)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ActionPermissionDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunOut])
async def list_task_runs(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> list[ScheduledTaskRunOut]:
    async with async_session_maker() as session:
        try:
            rows = await _svc.list_runs(_scope(ctx), session, task_id)
        except ActionNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
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
