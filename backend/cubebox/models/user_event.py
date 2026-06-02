"""UserEvent — user-scoped async notification (memory updates, etc.)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase
from cubebox.models.public_id import PREFIX_USER_EVENT


class UserEventType(StrEnum):
    MEMORY_UPDATED = "memory_updated"


class UserEvent(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_USER_EVENT
    __tablename__ = "user_events"
    __table_args__ = (
        Index("ix_user_events_user_created", "user_id", "created_at"),
        Index("ix_user_events_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20)
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", max_length=20)
    type: UserEventType = Field()
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    read_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
