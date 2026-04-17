"""UserSandbox model for tracking sandbox instances per user+workspace."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubebox.models.mixins import OrgScopedMixin


class UserSandbox(SQLModel, OrgScopedMixin, table=True):
    """Tracks sandbox instances bound to (user_id, workspace_id).

    Identity = user_id + workspace_id; one user can have one running
    sandbox per workspace. This fixes the cross-workspace isolation
    bug where a user with two workspaces previously shared one sandbox.
    """

    __tablename__ = "user_sandboxes"
    __table_args__ = (
        Index("ix_user_sandboxes_user_ws_status", "user_id", "workspace_id", "status"),
        Index("ix_user_sandboxes_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    user_id: str = Field(max_length=64, index=True)
    sandbox_id: str = Field(max_length=255, unique=True)
    status: str = Field(default="running", max_length=20)
    image: str = Field(max_length=512)
    volumes_config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = Field(default=3600)
