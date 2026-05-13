"""Conversation model."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class Conversation(CubeboxBase, OrgScopedMixin, table=True):
    """Conversation model for storing chat sessions.

    Deletion is soft: `delete_conversation` stamps `deleted_at` instead
    of issuing a SQL DELETE, so child rows (billing_events, artifacts,
    attachments) keep their FK target intact and cost history survives.
    Repository reads filter `deleted_at IS NULL` so soft-deleted rows
    are invisible to the API.
    """

    _PREFIX: ClassVar[str] = "conv"
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_ws", "creator_user_id", "workspace_id"),)

    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    title: str = Field(max_length=255)
    has_messages: bool = Field(default=False, index=True)
    is_pinned: bool = Field(default=False)
    deleted_at: datetime | None = Field(default=None, index=True)
