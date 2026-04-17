"""Conversation model."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubebox.models.mixins import OrgScopedMixin


class Conversation(SQLModel, OrgScopedMixin, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_org_ws", "org_id", "workspace_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    title: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
