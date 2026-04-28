"""Attachment model for per-conversation file uploads."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubebox.models.mixins import OrgScopedMixin


class Attachment(SQLModel, OrgScopedMixin, table=True):
    """A user-uploaded file scoped to a single conversation.

    Status state machine:
      pending  - uploaded but not yet referenced by any sent message
      attached - referenced by at least one sent message (immutable from this point)
    Deletion is physical (no soft-delete state).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_conv_status", "conversation_id", "status"),
        Index("ix_attachments_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    uploader_user_id: str = Field(max_length=36)

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
    attached_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
