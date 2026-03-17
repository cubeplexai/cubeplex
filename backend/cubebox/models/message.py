"""Message model."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Message(SQLModel, table=True):
    """Message model for storing conversation messages."""

    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str = Field(max_length=20)  # "user" | "assistant"
    content: str = Field(sa_column=Column(Text))
    events: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
