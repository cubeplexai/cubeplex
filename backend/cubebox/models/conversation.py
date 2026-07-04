"""Conversation model."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.reasoning import DEFAULT_REASONING


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
    __table_args__ = (
        Index("ix_conversations_user_ws", "creator_user_id", "workspace_id"),
        # Partial index sized to the tiny minority of soft-deleted rows —
        # gives a future GC job ("purge older than N days") a cheap range
        # scan without bloating writes on the hot live-row path. A plain
        # index on `deleted_at` would be skipped by the planner (most rows
        # NULL → poor selectivity) and pure index bloat.
        Index(
            "ix_conversations_deleted_at_partial",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
        Index("ix_conversations_topic", "topic_id"),
    )

    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    topic_id: str | None = Field(
        default=None, foreign_key="topics.id", max_length=20, nullable=True
    )
    title: str = Field(max_length=255)
    has_messages: bool = Field(default=False, index=True)
    is_pinned: bool = Field(default=False)
    is_group_chat: bool = Field(default=False)
    # Per-conversation model setting the user last sent with. ``model_key`` is
    # the stable selection key (a tier name like "pro" or a custom-preset
    # label), NULL meaning "use the workspace default". ``reasoning`` stores
    # the provider-independent ReasoningControl JSON.
    model_key: str | None = Field(default=None, max_length=64)
    reasoning: dict[str, Any] = Field(
        default_factory=lambda: dict(DEFAULT_REASONING),
        sa_column=Column(JSON),
    )
    # Source metadata (e.g. IM-bot linkage under an "im" key). Mirrors
    # Topic.attributes; see docs/dev/specs/2026-06-23-im-bot-settings-design.md.
    attributes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
