"""Topic and TopicParticipant models."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin, org_scope_index


class Topic(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "top"
    __tablename__ = "topics"
    __table_args__ = (
        org_scope_index("topics"),
        Index("ix_topics_creator", "creator_user_id", "workspace_id"),
    )

    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, max_length=20)
    # Source metadata. IM-created topics carry an "im" sub-object (platform,
    # account_id, bot_name, bot_avatar_url, channel_name, scope_kind); its
    # presence is the IM-origin marker read by im/worker.py + im/resume.py.
    # See docs/dev/specs/2026-06-23-im-bot-settings-design.md.
    attributes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    max_participants: int = Field(default=20)
    is_archived: bool = Field(default=False)
    is_pinned: bool = Field(default=False)
    # Bumped on every message insert into any child conversation. Drives
    # sidebar ordering ("topic with the most recent message floats up").
    # Without this column, topics rank by Topic.updated_at which only
    # changes on metadata edits — topics would appear frozen in the sidebar
    # after the first message. Default to created_at via Python; the DB
    # default is a literal `now()` (Postgres) / CURRENT_TIMESTAMP (sqlite).
    last_activity_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
    )


class TopicParticipant(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "tpm"
    __tablename__ = "topic_participants"
    __table_args__ = (
        UniqueConstraint("topic_id", "user_id", name="uq_topic_participant"),
        Index("ix_topic_participants_user", "user_id"),
    )

    topic_id: str = Field(foreign_key="topics.id", max_length=20, index=True)
    user_id: str = Field(foreign_key="users.id", max_length=20)
    role: str = Field(default="member", max_length=20)
    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True)),
    )
