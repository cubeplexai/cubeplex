"""Inflight sandbox commands owned by CubePlex (not the provider)."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin, org_scope_index
from cubeplex.models.public_id import PREFIX_SANDBOX_COMMAND


class SandboxCommandStatus(StrEnum):
    starting = "starting"
    running = "running"
    exited = "exited"
    killed = "killed"


class SandboxCommandNoticeState(StrEnum):
    none = "none"
    pending = "pending"
    delivered = "delivered"


class SandboxCommand(CubeplexBase, OrgScopedMixin, table=True):
    """Process index for managed execute (in-run in v1)."""

    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_COMMAND
    __tablename__ = "sandbox_commands"
    __table_args__ = (
        org_scope_index("sandbox_commands"),
        Index("ix_sandbox_commands_sandbox_status", "user_sandbox_id", "status"),
        Index("ix_sandbox_commands_run_status", "run_id", "status"),
    )

    user_sandbox_id: str = Field(
        foreign_key="user_sandboxes.id",
        max_length=20,
        index=True,
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
