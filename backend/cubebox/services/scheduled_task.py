"""Stateless service for scheduled-task CRUD and lifecycle operations.

Extracted from route handlers so that both HTTP routes and agent tool
front-doors can share the same validated business logic. Every mutating
method owns its transaction (calls ``session.commit()`` exactly once).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
)
from cubebox.api.schemas.ws_scheduled_tasks import (
    _validate_cron,
    _validate_timezone,
)
from cubebox.models import Role
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.repositories.conversation import ConversationRepository
from cubebox.schedules.compute import (
    as_utc,
    latest_due_before,
    next_fire_after,
)
from cubebox.services.schedule_target_spec import (
    ScheduleTargetError,
    validate_destination_scope,
)

# Destination columns whose value is fixed at create time. Both REST and
# agent paths share this lock so a PATCH (no matter the source) cannot
# silently change where a schedule's runs land.
_FORBIDDEN_PATCH_DESTINATION_FIELDS: frozenset[str] = frozenset(
    {
        "target_mode",
        "target_conversation_id",
        "im_account_id",
        "im_channel_id",
        "im_scope_key",
        "im_scope_kind",
    }
)

# Fields whose change requires recomputing next_fire_at.  Editing only
# name/prompt/target_* must NOT slide the schedule.
_SCHEDULE_FIELDS: frozenset[str] = frozenset(
    {"schedule_kind", "cron_expr", "interval_seconds", "run_at", "timezone"}
)


def _to_utc_naive(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC naive for storage."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


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


class ScheduledTaskService:
    """Stateless service — no constructor args.

    Every public method takes ``(ctx, session, ...)`` so the caller
    controls both identity and connection lifetime.
    """

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_tasks(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        *,
        topic_id: str | None = None,
        im_account_id: str | None = None,
        im_channel_id: str | None = None,
    ) -> list[ScheduledTask]:
        stmt = (
            select(ScheduledTask)
            .where(
                ScheduledTask.org_id == ctx.org_id,  # type: ignore[arg-type]
                ScheduledTask.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
                cast(Any, ScheduledTask.deleted_at).is_(None),
            )
            .order_by(
                ScheduledTask.created_at.desc(),  # type: ignore[attr-defined]
            )
            .limit(100)
        )
        if topic_id is not None:
            stmt = stmt.where(ScheduledTask.topic_id == topic_id)  # type: ignore[arg-type]
        if im_account_id is not None:
            stmt = stmt.where(ScheduledTask.im_account_id == im_account_id)  # type: ignore[arg-type]
        if im_channel_id is not None:
            stmt = stmt.where(ScheduledTask.im_channel_id == im_channel_id)  # type: ignore[arg-type]
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_task(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        return await self._load(ctx, session, task_id)

    async def list_runs(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> list[ScheduledTaskRun]:
        # Validate task exists first
        await self._load(ctx, session, task_id)
        stmt = (
            select(ScheduledTaskRun)
            .where(
                ScheduledTaskRun.org_id == ctx.org_id,  # type: ignore[arg-type]
                ScheduledTaskRun.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
                ScheduledTaskRun.scheduled_task_id == task_id,  # type: ignore[arg-type]
            )
            .order_by(
                ScheduledTaskRun.scheduled_for.desc(),  # type: ignore[attr-defined]
            )
            .limit(50)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        data: dict[str, Any],
    ) -> ScheduledTask:
        self._validate_create(data)

        # Conversation ownership check for fixed target
        if data.get("target_mode") == "fixed":
            await self._check_conversation_ownership(
                session, ctx, data.get("target_conversation_id", "")
            )

        # Cross-workspace FK guard: the FK constraints only check that the
        # row exists; they don't enforce that it lives in this workspace.
        try:
            await validate_destination_scope(
                session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                topic_id=data.get("topic_id"),
                im_account_id=data.get("im_account_id"),
            )
        except ScheduleTargetError as exc:
            raise ActionInvalidInput(str(exc)) from exc

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
            run_at=_to_utc_naive(run_at) if run_at is not None else None,
            end_at=_to_utc_naive(end_at) if end_at is not None else None,
            timezone=data.get("timezone", "UTC"),
            target_mode=data["target_mode"],
            target_conversation_id=data.get("target_conversation_id"),
            topic_id=data.get("topic_id"),
            im_account_id=data.get("im_account_id"),
            im_channel_id=data.get("im_channel_id"),
            im_scope_key=data.get("im_scope_key"),
            im_scope_kind=data.get("im_scope_kind"),
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
        task = await self._load_for_mutation(ctx, session, task_id)
        self._validate_patch(data)

        # Destination immutability — defense in depth. The HTTP layer already
        # blocks these via Pydantic's ``model_fields_set`` gate, but the
        # legacy ``scheduled_tasks_update`` agent action sends a raw data
        # dict and would otherwise bypass that. Reject here so every caller
        # (route + tool + future action) hits the same wall.
        forbidden = _FORBIDDEN_PATCH_DESTINATION_FIELDS & data.keys()
        if forbidden:
            raise ActionInvalidInput(
                f"Cannot change destination fields after creation: {sorted(forbidden)}. "
                "Delete the schedule and create a new one instead."
            )

        # topic_id is patchable only when the existing row is new_each_run;
        # for fixed/im_channel a topic_id is structurally meaningless.
        if "topic_id" in data and task.target_mode != "new_each_run":
            raise ActionInvalidInput(
                f"topic_id can only be patched when target_mode='new_each_run' "
                f"(current target_mode={task.target_mode!r})"
            )

        # Cross-workspace FK guard for the one destination field PATCH can
        # change (topic_id). im_account_id is in the forbidden set above, so
        # we never need to re-check it here.
        if "topic_id" in data and data["topic_id"] is not None:
            try:
                await validate_destination_scope(
                    session,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    topic_id=data.get("topic_id"),
                    im_account_id=None,
                )
            except ScheduleTargetError as exc:
                raise ActionInvalidInput(str(exc)) from exc

        touched_schedule = False
        for field in (
            "name",
            "prompt",
            "schedule_kind",
            "cron_expr",
            "interval_seconds",
            "run_at",
            "timezone",
        ):
            val = data.get(field)
            if val is None:
                continue
            if field == "run_at":
                val = _to_utc_naive(val)
            if field in _SCHEDULE_FIELDS and val != getattr(task, field):
                touched_schedule = True
            setattr(task, field, val)

        # topic_id is the only destination-related field a PATCH can touch.
        # `None` is a valid (clearing) value, so use the `in data` check.
        if "topic_id" in data:
            task.topic_id = data["topic_id"]

        # end_at explicit null handling: must be handled separately because
        # the generic loop skips None values.
        if "end_at" in data:
            raw_end = data["end_at"]
            task.end_at = _to_utc_naive(raw_end) if raw_end is not None else None

        # Only recompute next_fire_at when schedule actually changed
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load(
        self,
        ctx: ScopeContext,
        session: AsyncSession,
        task_id: str,
    ) -> ScheduledTask:
        stmt = select(ScheduledTask).where(
            ScheduledTask.org_id == ctx.org_id,  # type: ignore[arg-type]
            ScheduledTask.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
            ScheduledTask.id == task_id,  # type: ignore[arg-type]
            cast(Any, ScheduledTask.deleted_at).is_(None),
        )
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()
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
        t: ScheduledTask,
    ) -> datetime | None:
        """Resume policy: account for occurrences due while paused.

        Mirrors the outage missed-run policy: at most ONE summary
        history row.
        """
        now = datetime.now(UTC)
        anchor = as_utc(t.next_fire_at) if t.next_fire_at is not None else None

        if t.schedule_kind == "once":
            run_at = as_utc(t.run_at) if t.run_at is not None else None
            if run_at is not None and run_at <= now:
                scheduled_for_naive = _to_utc_naive(run_at)
                try:
                    async with session.begin_nested():
                        session.add(
                            ScheduledTaskRun(
                                scheduled_task_id=t.id,
                                org_id=t.org_id,
                                workspace_id=t.workspace_id,
                                scheduled_for=scheduled_for_naive,
                                claimed_at=now,
                                state="skipped_missed",
                                detail=("paused past its one-shot fire time"),
                            )
                        )
                        await session.flush()
                except IntegrityError:
                    pass
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
                detail=(f"paused: skipped {anchor.isoformat()}..{latest_due.isoformat()}"),
            )
        )
        return next_fire_after(
            kind=t.schedule_kind,
            after=latest_due,
            cron_expr=t.cron_expr,
            interval_seconds=t.interval_seconds,
            tz=t.timezone,
        )

    @staticmethod
    def _validate_create(data: dict[str, Any]) -> None:
        tz = data.get("timezone", "UTC")
        try:
            _validate_timezone(tz)
        except ValueError as exc:
            raise ActionInvalidInput(str(exc)) from exc

        kind = data.get("schedule_kind")
        if kind == "cron":
            expr = data.get("cron_expr")
            if not expr:
                raise ActionInvalidInput("cron_expr required for cron schedule")
            try:
                _validate_cron(expr)
            except ValueError as exc:
                raise ActionInvalidInput(str(exc)) from exc
        if kind == "interval" and not data.get("interval_seconds"):
            raise ActionInvalidInput("interval_seconds required for interval schedule")
        if kind == "once":
            if data.get("run_at") is None:
                raise ActionInvalidInput("run_at required for once schedule")

        run_at = data.get("run_at")
        if isinstance(run_at, datetime) and run_at.tzinfo is None:
            raise ActionInvalidInput("run_at must include a timezone offset")
        end_at = data.get("end_at")
        if isinstance(end_at, datetime) and end_at.tzinfo is None:
            raise ActionInvalidInput("end_at must include a timezone offset")

        if not data.get("name"):
            raise ActionInvalidInput("name is required")
        if not data.get("prompt"):
            raise ActionInvalidInput("prompt is required")
        if not data.get("target_mode"):
            raise ActionInvalidInput("target_mode is required")
        if data.get("target_mode") == "fixed" and not data.get("target_conversation_id"):
            raise ActionInvalidInput("target_conversation_id required when target_mode=fixed")

    @staticmethod
    def _validate_patch(data: dict[str, Any]) -> None:
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

        kind = data.get("schedule_kind")
        if kind == "cron" and not data.get("cron_expr"):
            raise ActionInvalidInput("cron_expr required when changing schedule_kind to cron")
        if kind == "interval" and not data.get("interval_seconds"):
            raise ActionInvalidInput(
                "interval_seconds required when changing schedule_kind to interval"
            )
        if kind == "once" and data.get("run_at") is None:
            raise ActionInvalidInput("run_at required when changing schedule_kind to once")

        run_at = data.get("run_at")
        if isinstance(run_at, datetime) and run_at.tzinfo is None:
            raise ActionInvalidInput("run_at must include a timezone offset")
        end_at = data.get("end_at")
        if isinstance(end_at, datetime) and end_at.tzinfo is None:
            raise ActionInvalidInput("end_at must include a timezone offset")
