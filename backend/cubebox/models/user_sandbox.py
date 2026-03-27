"""UserSandbox model for tracking sandbox instances per user."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class UserSandbox(SQLModel, table=True):
    """Tracks sandbox instances bound to users.

    Each user can have one active (running) sandbox at a time.
    The schema supports future expansion to multiple sandboxes per user.
    """

    __tablename__ = "user_sandboxes"
    __table_args__ = (Index("ix_user_sandboxes_user_status", "user_id", "status"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    user_id: str = Field(max_length=64, index=True)
    sandbox_id: str = Field(max_length=255, unique=True)
    status: str = Field(default="running", max_length=20)  # running / terminated / error
    image: str = Field(max_length=512)
    volumes_config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = Field(default=3600)
