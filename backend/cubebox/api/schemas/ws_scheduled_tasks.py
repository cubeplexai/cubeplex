from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, model_validator

ScheduleKind = Literal["cron", "interval", "once"]
TargetMode = Literal["fixed", "new_each_run"]


def _validate_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {tz!r}") from exc


def _validate_cron(expr: str) -> None:
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
        if self.target_mode == "fixed" and not self.target_conversation_id:
            raise ValueError("target_conversation_id required when target_mode=fixed")
        return self


class ScheduledTaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str | None = None
    target_mode: TargetMode | None = None
    target_conversation_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ScheduledTaskPatch:
        if self.timezone is not None:
            _validate_timezone(self.timezone)
        if self.cron_expr is not None:
            _validate_cron(self.cron_expr)
        if self.run_at is not None and self.run_at.tzinfo is None:
            raise ValueError("run_at must include a timezone offset")
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
    owner_user_id: str
    next_fire_at: str | None
    last_fired_at: str | None
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
