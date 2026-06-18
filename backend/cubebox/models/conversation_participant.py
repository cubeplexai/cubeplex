"""Conversation participant — per-conversation actor list."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class ConversationParticipant(CubeboxBase, OrgScopedMixin, table=True):
    """A user who has actively participated in a conversation.

    Append-only: rows are created on first send and never removed. SSE
    subscription is governed separately (see access control matrix in
    docs/dev/specs/2026-06-18-conversation-participants-design.md).
    """

    _PREFIX: ClassVar[str] = "cpm"
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),
        Index("ix_conversation_participants_user", "user_id"),
    )

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)
    user_id: str = Field(foreign_key="users.id", max_length=20)
    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
