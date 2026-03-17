"""Conversation model."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Conversation(SQLModel, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    title: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
