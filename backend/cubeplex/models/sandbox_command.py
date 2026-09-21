"""Inflight sandbox commands owned by CubePlex (not the provider)."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin, org_scope_index
from cubeplex.models.public_id import PREFIX_SANDBOX_COMMAND, PREFIX_SANDBOX_COMMAND_WAKE


class SandboxCommandStatus(StrEnum):
    starting = "starting"
    not_started = "not_started"
    running = "running"
    exited = "exited"
    killed = "killed"


class SandboxCommandNoticeState(StrEnum):
    none = "none"
    pending = "pending"
    delivered = "delivered"


class SandboxCommandKind(StrEnum):
    execute = "execute"
    monitor = "monitor"


class SandboxCommandLifetime(StrEnum):
    run = "run"
    conversation = "conversation"


class SandboxCommand(CubeplexBase, OrgScopedMixin, table=True):
    """Process index for managed execute and monitors."""

    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_COMMAND
    __tablename__ = "sandbox_commands"
    __table_args__ = (
        org_scope_index("sandbox_commands"),
        Index("ix_sandbox_commands_sandbox_status", "user_sandbox_id", "status"),
        Index("ix_sandbox_commands_run_status", "run_id", "status"),
        Index("ix_sandbox_commands_conv_status", "conversation_id", "status"),
        UniqueConstraint("task_id", name="uq_sandbox_commands_task_id"),
    )

    user_sandbox_id: str = Field(
        foreign_key="user_sandboxes.id",
        max_length=20,
        index=True,
    )
    # Nullable during expand/backfill; never infer an old instance from the current row.
    task_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(20),
            ForeignKey("background_tasks.id", name="fk_sandbox_commands_task_id"),
            nullable=True,
        ),
    )
    sandbox_instance_id: str | None = Field(default=None, max_length=255)
    start_token: str | None = Field(default=None, max_length=64)
    start_requested_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    log_state: str = Field(
        default="pending", max_length=20, sa_column_kwargs={"server_default": "pending"}
    )
    conversation_id: str = Field(max_length=20, index=True)
    run_id: str = Field(max_length=64, index=True)
    tool_call_id: str = Field(max_length=128, default="")
    started_by_user_id: str = Field(max_length=20)
    agent_id: str | None = Field(default=None, max_length=64)
    command: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", max_length=512)
    provider: str = Field(default="opensandbox", max_length=32)
    provider_ref: str | None = Field(default=None, max_length=255)
    status: str = Field(default=SandboxCommandStatus.starting.value, max_length=20)
    notify_on_complete: bool = Field(default=True)
    notice_state: str = Field(
        default=SandboxCommandNoticeState.none.value,
        max_length=20,
    )
    log_path: str = Field(default="", max_length=512)
    log_cursor: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    exit_code: int | None = Field(default=None)
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    owner_id: str | None = Field(default=None, max_length=64)
    owner_until: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    kind: str = Field(
        default=SandboxCommandKind.execute.value,
        sa_column=Column(
            String(20), nullable=False, server_default=SandboxCommandKind.execute.value
        ),
    )
    lifetime: str = Field(
        default=SandboxCommandLifetime.run.value,
        sa_column=Column(
            String(20), nullable=False, server_default=SandboxCommandLifetime.run.value
        ),
    )
    notify_run_id: str | None = Field(default=None, max_length=64)
    wake_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    wake_drops: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    line_wakes_disabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    flood_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    monitor_deadline_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class SandboxCommandWakeState(StrEnum):
    pending = "pending"
    claimed = "claimed"
    delivered = "delivered"


class SandboxCommandWake(CubeplexBase, OrgScopedMixin, table=True):
    """Durable outbox for monitor/execute completion wakes."""

    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_COMMAND_WAKE
    __tablename__ = "sandbox_command_wakes"
    __table_args__ = (
        org_scope_index("sandbox_command_wakes"),
        Index("ix_sandbox_command_wakes_cmd_state", "command_id", "state"),
        Index(
            "ix_sandbox_command_wakes_claim",
            "state",
            "owner_until",
            "created_at",
            "id",
        ),
        UniqueConstraint("dedupe_key", name="uq_sandbox_command_wakes_dedupe_key"),
    )

    command_id: str = Field(foreign_key="sandbox_commands.id", max_length=20, index=True)
    conversation_id: str = Field(max_length=20, index=True)
    reason: str = Field(max_length=16)
    dedupe_key: str = Field(max_length=96)
    text_tail: str = Field(default="", sa_column=Column(Text, nullable=False))
    state: str = Field(default=SandboxCommandWakeState.pending.value, max_length=20)
    owner_id: str | None = Field(default=None, max_length=64)
    owner_until: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    delivery_run_id: str | None = Field(default=None, max_length=64)
    delivery_steer_id: str | None = Field(default=None, max_length=64)
    started_by_user_id: str = Field(max_length=20)
