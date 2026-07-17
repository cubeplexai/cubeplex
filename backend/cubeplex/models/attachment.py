"""Attachment model for per-conversation file uploads."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin


class Attachment(CubeplexBase, OrgScopedMixin, table=True):
    """A user-uploaded file scoped to a single conversation.

    Status state machine:
      pending  - uploaded but not yet referenced by any sent message
      attached - referenced by at least one sent message (immutable from this point)
    Deletion is physical (no soft-delete state).
    """

    _PREFIX: ClassVar[str] = "atch"
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_conv_status", "conversation_id", "status"),
        Index("ix_attachments_org_ws", "org_id", "workspace_id"),
    )

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)
    uploader_user_id: str = Field(foreign_key="users.id", max_length=20)

    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=128)
    size_bytes: int
    kind: str = Field(max_length=16)

    object_key: str = Field(max_length=1024)
    sandbox_path: str = Field(max_length=1024)
    thumbnail_object_key: str | None = Field(default=None, max_length=1024)

    width: int | None = None
    height: int | None = None

    status: str = Field(default="pending", max_length=16)
    attached_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
