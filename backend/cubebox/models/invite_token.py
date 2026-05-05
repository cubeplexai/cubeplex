"""Invite token — single-use, time-limited workspace invitation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


def _default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


class InviteToken(SQLModel, table=True):
    __tablename__ = "invite_tokens"
    __table_args__ = (Index("ix_invite_tokens_expires", "expires_at"),)

    token: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=64)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20)
    role: str = Field(max_length=32)
    created_by: str = Field(foreign_key="users.id", max_length=20)
    expires_at: datetime = Field(default_factory=_default_expiry)
    used_at: datetime | None = Field(default=None)
