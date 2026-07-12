from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, model_validator

from cubeplex.services.schedule_target_spec import (
    ScheduleTargetError,
    ScheduleTargetSpec,
)

ScheduleKind = Literal["cron", "interval", "once"]
TargetMode = Literal["fixed", "new_each_run", "im_channel"]


def _validate_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {tz!r}") from exc


def _validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields "
            f"(minute hour day month weekday), got {len(parts)}: {expr!r}"
        )
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")


class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
    schedule_kind: ScheduleKind
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str = "UTC"
    target_mode: TargetMode
    target_conversation_id: str | None = None
    # Destination fields for new_each_run + im_channel target modes.
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> ScheduledTaskCreate:
        _validate_timezone(self.timezone)
        if self.schedule_kind == "cron":
            if not self.cron_expr:
                raise ValueError("cron_expr required for cron schedule")
            _validate_cron(self.cron_expr)
        if self.schedule_kind == "interval" and not self.interval_seconds:
            raise ValueError("interval_seconds required for interval schedule")
        if self.schedule_kind == "once":
            if self.run_at is None:
                raise ValueError("run_at required for once schedule")
            if self.run_at.tzinfo is None:
                raise ValueError("run_at must include a timezone offset")
        if self.end_at is not None and self.end_at.tzinfo is None:
            raise ValueError("end_at must include a timezone offset")
        # Per-mode destination shape: single source of truth in
        # ScheduleTargetSpec; pydantic surfaces the message as a 422.
        try:
            ScheduleTargetSpec(
                target_mode=self.target_mode,
                target_conversation_id=self.target_conversation_id,
                topic_id=self.topic_id,
                im_account_id=self.im_account_id,
                im_channel_id=self.im_channel_id,
                im_scope_key=self.im_scope_key,
                im_scope_kind=self.im_scope_kind,
            ).validate()
        except ScheduleTargetError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ScheduledTaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule_kind: ScheduleKind | None = None
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str | None = None
    # PATCH does NOT support changing the destination shape (target_mode,
    # target_conversation_id, im_*). The fields are declared here so the
    # route can detect any attempt and reject it with a 422 via
    # model_fields_set membership — null payloads must also be rejected.
    target_mode: TargetMode | None = None
    target_conversation_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None
    # topic_id IS patchable, but only when the existing row uses
    # target_mode="new_each_run" — the route enforces that check.
    topic_id: str | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> ScheduledTaskPatch:
        if self.timezone is not None:
            _validate_timezone(self.timezone)
        if self.cron_expr is not None:
            _validate_cron(self.cron_expr)
        if self.run_at is not None and self.run_at.tzinfo is None:
            raise ValueError("run_at must include a timezone offset")
        if self.end_at is not None and self.end_at.tzinfo is None:
            raise ValueError("end_at must include a timezone offset")
        # When changing schedule_kind, require the matching configuration
        # field in the same patch so the route can recompute next_fire_at
        # against an internally-consistent task. Cross-kind switches that
        # forget e.g. cron_expr would otherwise silently drop fires.
        if self.schedule_kind == "cron" and not self.cron_expr:
            raise ValueError("cron_expr required when changing schedule_kind to cron")
        if self.schedule_kind == "interval" and not self.interval_seconds:
            raise ValueError("interval_seconds required when changing schedule_kind to interval")
        if self.schedule_kind == "once" and self.run_at is None:
            raise ValueError("run_at required when changing schedule_kind to once")
        return self


class ScheduledTaskRetarget(BaseModel):
    """Whole-package destination replacement (not a partial PATCH).

    ``im_channel`` does not accept free-form im_* fields — the server
    resolves them from ``anchor_conversation_id`` and/or the task's current
    conversation/topic ``IMThreadLink``. If no binding is found, the request
    fails with 422.
    """

    target_mode: TargetMode
    target_conversation_id: str | None = None
    topic_id: str | None = None
    # Optional explicit anchor when retargeting to im_channel (e.g. the
    # conversation the user is editing from). Falls back to the task's
    # existing fixed conversation or topic link.
    anchor_conversation_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ScheduledTaskRetarget:
        if self.target_mode == "fixed" and not self.target_conversation_id:
            raise ValueError("target_conversation_id is required when target_mode='fixed'")
        if self.target_mode == "im_channel":
            # im_* are resolved server-side; client must not send them.
            return self
        # Shape-check fixed / new_each_run the same way as create (im empty).
        try:
            ScheduleTargetSpec(
                target_mode=self.target_mode,
                target_conversation_id=self.target_conversation_id,
                topic_id=self.topic_id if self.target_mode == "new_each_run" else None,
                im_account_id=None,
                im_channel_id=None,
                im_scope_key=None,
                im_scope_kind=None,
            ).validate()
        except ScheduleTargetError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ScheduledTaskOut(BaseModel):
    id: str
    name: str
    status: str
    schedule_kind: str
    cron_expr: str | None
    interval_seconds: int | None
    run_at: str | None
    timezone: str
    prompt: str
    target_mode: str
    target_conversation_id: str | None
    topic_id: str | None
    im_account_id: str | None
    im_channel_id: str | None
    im_scope_key: str | None
    im_scope_kind: str | None
    owner_user_id: str
    next_fire_at: str | None
    last_fired_at: str | None
    end_at: str | None
    created_at: str
    updated_at: str


class ScheduledTaskRunOut(BaseModel):
    id: str
    scheduled_for: str
    claimed_at: str
    started_at: str | None
    state: str
    retry_count: int
    next_retry_at: str | None
    run_id: str | None
    conversation_id: str | None
    detail: str | None


class ScheduledTaskListOut(BaseModel):
    tasks: list[ScheduledTaskOut]
