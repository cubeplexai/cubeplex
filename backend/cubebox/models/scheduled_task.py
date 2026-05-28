"""Scheduled-task tables.

``ScheduledTask`` is the schedule definition; ``ScheduledTaskRun`` is the
per-occurrence history row. The unique ``(scheduled_task_id, scheduled_for)``
constraint on the history table is the occurrence-idempotency key: inserting it
is the act that claims an occurrence, so two racing pollers produce one row.
Soft delete mirrors ``Conversation`` (stamp ``deleted_at``; the poller filters
``deleted_at IS NULL``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, Index, Integer, UniqueConstraint, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class ScheduledTask(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "stask"
    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        Index("ix_scheduled_tasks_org_ws", "org_id", "workspace_id"),
        Index("ix_scheduled_tasks_status_next_fire", "status", "next_fire_at"),
        Index(
            "ix_scheduled_tasks_deleted_at_partial",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )

    owner_user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    name: str = Field(max_length=255)
    status: str = Field(default="active", max_length=16)  # active | paused

    schedule_kind: str = Field(max_length=16)  # cron | interval | once
    cron_expr: str | None = Field(default=None, max_length=255)
    interval_seconds: int | None = Field(default=None)
    run_at: datetime | None = Field(default=None)
    timezone: str = Field(default="UTC", max_length=64)

    prompt: str = Field()

    target_mode: str = Field(max_length=16)  # fixed | new_each_run
    target_conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", max_length=20
    )

    next_fire_at: datetime | None = Field(default=None, index=True)
    last_fired_at: datetime | None = Field(default=None)
    deleted_at: datetime | None = Field(default=None)


class ScheduledTaskRun(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "stkrn"
    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        Index("ix_scheduled_task_runs_org_ws", "org_id", "workspace_id"),
        UniqueConstraint("scheduled_task_id", "scheduled_for", name="uq_stkrn_task_scheduled_for"),
        Index("ix_stkrn_state_claimed_at", "state", "claimed_at"),
        Index("ix_stkrn_run_id", "run_id"),
    )

    scheduled_task_id: str = Field(foreign_key="scheduled_tasks.id", max_length=20, index=True)
    scheduled_for: datetime = Field()
    claimed_at: datetime = Field()
    started_at: datetime | None = Field(default=None)
    # claimed | started | succeeded | failed | skipped_missed |
    # skipped_busy_max_retries
    state: str = Field(max_length=32)
    claim_count: int = Field(default=1)
    retry_count: int = Field(
        default=0,
        sa_column=Column(Integer(), nullable=False, server_default="0"),
    )
    next_retry_at: datetime | None = Field(default=None, nullable=True)
    run_id: str | None = Field(default=None, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=20)
    detail: str | None = Field(default=None)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
