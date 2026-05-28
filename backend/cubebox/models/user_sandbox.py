"""UserSandbox model for tracking sandbox instances per user+workspace."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Column, Index, text
from sqlalchemy.types import JSON
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class UserSandbox(CubeboxBase, OrgScopedMixin, table=True):
    """Tracks sandbox instances bound to (user_id, workspace_id).

    Identity = user_id + workspace_id; one user can have one running
    sandbox per workspace. This fixes the cross-workspace isolation
    bug where a user with two workspaces previously shared one sandbox.
    """

    _PREFIX: ClassVar[str] = "sbx"
    __tablename__ = "user_sandboxes"
    __table_args__ = (
        Index("ix_user_sandboxes_user_ws_status", "user_id", "workspace_id", "status"),
        Index("ix_user_sandboxes_org_ws", "org_id", "workspace_id"),
        Index(
            "uq_user_sandbox_active",
            "org_id",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('provisioning','running')"),
            sqlite_where=text("status IN ('provisioning','running')"),
        ),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    sandbox_id: str = Field(max_length=255, unique=True)
    status: str = Field(default="running", max_length=20)
    image: str = Field(max_length=512)
    volumes_config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = Field(default=3600)
    # server_default is required so the autogen migration backfills existing
    # non-null rows; a Python-side default alone won't touch rows already there.
    provider: str = Field(
        default="opensandbox",
        max_length=32,
        sa_column_kwargs={"server_default": "opensandbox"},
    )
    paused_at: datetime | None = Field(default=None)
    paused_ttl_seconds: int = Field(
        default=24 * 60,
        sa_column_kwargs={"server_default": "1440"},
    )
    last_resumed_at: datetime | None = Field(default=None)
    in_use_until: datetime | None = Field(default=None, index=True)
    last_provider_check: datetime | None = Field(default=None, index=True)
