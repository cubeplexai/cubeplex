"""Execution facts and result delivery are independent durable records."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, Text, UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin, org_scope_index
from cubeplex.models.public_id import PREFIX_BACKGROUND_TASK, PREFIX_BACKGROUND_TASK_EVENT


class BackgroundTaskState(StrEnum):
    starting = "starting"
    running = "running"
    waiting_input = "waiting_input"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"


TERMINAL_TASK_STATES = frozenset(
    {BackgroundTaskState.succeeded, BackgroundTaskState.failed, BackgroundTaskState.cancelled}
)
INFLIGHT_TASK_STATES = frozenset(set(BackgroundTaskState) - TERMINAL_TASK_STATES)


class TaskStopReason(StrEnum):
    user_stop = "user_stop"
    conversation_stop = "conversation_stop"
    conversation_deleted = "conversation_deleted"
    deadline = "deadline"


class BackgroundTaskEventState(StrEnum):
    pending = "pending"
    claimed = "claimed"
    delivered = "delivered"
    discarded = "discarded"


class BackgroundTask(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_BACKGROUND_TASK
    __tablename__ = "background_tasks"
    __table_args__ = (
        org_scope_index("background_tasks"),
        Index("ix_background_tasks_conv_state", "conversation_id", "state"),
        Index("ix_background_tasks_claim", "state", "owner_until", "id"),
        CheckConstraint("execution_generation >= 0", name="ck_background_task_generation"),
        CheckConstraint("revision >= 0", name="ck_background_task_revision"),
        CheckConstraint("parent_task_id IS NULL OR parent_task_id <> id", name="ck_task_parent"),
        UniqueConstraint(
            "admission_id",
            "originating_run_id",
            "tool_call_id",
            "agent_id",
            name="uq_background_task_tool_call",
            postgresql_nulls_not_distinct=True,
        ),
    )

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    admission_id: str = Field(foreign_key="conversation_execution_admissions.id", max_length=20)
    parent_task_id: str | None = Field(
        default=None, foreign_key="background_tasks.id", max_length=20, index=True
    )
    kind: str = Field(max_length=32)
    description: str = Field(default="", max_length=512)
    originating_run_id: str = Field(max_length=64)
    tool_call_id: str = Field(max_length=128)
    agent_id: str | None = Field(default=None, max_length=64)
    started_by_user_id: str = Field(foreign_key="users.id", max_length=20)
    execution_generation: int = Field(sa_column=Column(BigInteger, nullable=False))
    state: str = Field(default=BackgroundTaskState.starting.value, max_length=20)
    notify_on_complete: bool = Field(default=True)
    deadline_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    stop_requested_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    stop_reason: str | None = Field(default=None, max_length=32)
    notifications_cancelled_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    owner_token: str | None = Field(default=None, max_length=64)
    owner_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    backgrounded_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    foreground_result_delivered_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_observed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    result_ref: str | None = Field(default=None, max_length=512)
    result_summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    revision: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))


class BackgroundTaskEvent(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_BACKGROUND_TASK_EVENT
    __tablename__ = "background_task_events"
    __table_args__ = (
        org_scope_index("background_task_events"),
        UniqueConstraint("task_id", "dedupe_key", name="uq_background_task_event_dedupe"),
        Index("ix_background_task_events_pending", "conversation_id", "state", "created_at", "id"),
        Index("ix_background_task_events_claim", "state", "owner_until", "id"),
    )

    task_id: str = Field(foreign_key="background_tasks.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    execution_generation: int = Field(sa_column=Column(BigInteger, nullable=False))
    reason: str = Field(max_length=32)
    dedupe_key: str = Field(max_length=128)
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    result_ref: str | None = Field(default=None, max_length=512)
    state: str = Field(default=BackgroundTaskEventState.pending.value, max_length=20)
    discard_reason: str | None = Field(default=None, max_length=64)
    owner_token: str | None = Field(default=None, max_length=64)
    owner_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    delivery_run_id: str | None = Field(default=None, max_length=64)
    delivery_attempt_id: str | None = Field(default=None, max_length=64)
    delivery_input_id: str | None = Field(default=None, max_length=128)
    checkpoint_run_id: str | None = Field(default=None, max_length=64)
    checkpoint_input_id: str | None = Field(default=None, max_length=128)
    delivered_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    revision: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
