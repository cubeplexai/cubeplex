"""Conversation share — immutable snapshot of a conversation."""

from typing import Any, ClassVar

from sqlalchemy import Column, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class ConversationShare(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "shr"
    __tablename__ = "conversation_shares"
    __table_args__ = (
        Index("ix_conversation_shares_org_ws", "org_id", "workspace_id"),
        Index("ix_conversation_shares_creator", "creator_user_id", "workspace_id"),
        Index("ix_conversation_shares_conversation", "conversation_id"),
    )

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    creator_display_name: str = Field(max_length=255)
    title: str = Field(max_length=255)
    snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    artifacts_snapshot: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False),
    )
    is_active: bool = Field(default=True)
