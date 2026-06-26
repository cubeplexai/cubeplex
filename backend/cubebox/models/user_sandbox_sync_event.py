"""Append-only audit log of skill sync attempts on user sandboxes.

Hot-path noop syncs are NOT recorded — only events that pushed, removed, or
failed land here. The latest successful event for a given UserSandbox row is
referenced by ``UserSandbox.last_skill_sync_event_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class UserSandboxSyncEvent(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "uss"
    __tablename__ = "user_sandbox_sync_events"

    user_sandbox_id: str = Field(foreign_key="user_sandboxes.id", max_length=20, index=True)
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    finished_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    status: str = Field(max_length=16)  # 'success' | 'failed'
    manifest_snapshot: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    n_pushed: int = Field(default=0)
    n_removed: int = Field(default=0)
    tar_size_bytes: int | None = Field(default=None, nullable=True)
    error_type: str | None = Field(default=None, max_length=64, nullable=True)
    error_message: str | None = Field(default=None, max_length=1024, nullable=True)

    __table_args__ = (
        Index("ix_uss_sandbox_started", "user_sandbox_id", "started_at"),
        Index("ix_uss_org_ws_started", "org_id", "workspace_id", "started_at"),
    )
