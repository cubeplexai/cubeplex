"""Conversation model."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import OrgScopedMixin
from cubebox.models.public_id import PREFIX_CONVERSATION, generate_public_id


class Conversation(SQLModel, OrgScopedMixin, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_ws", "creator_user_id", "workspace_id"),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_CONVERSATION),
        primary_key=True,
        max_length=20,
    )
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    title: str = Field(max_length=255)
    has_messages: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
