"""Async work queue for embedding chunks (Postgres-only, no Redis)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import BigInteger, Column, DateTime, Index, Text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_EMBEDDING_JOB, generate_public_id


class EmbeddingJobState(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    dead = "dead"


class EmbeddingJob(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_EMBEDDING_JOB
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        Index("ix_ejob_pending", "state", "scheduled_at"),
        Index("ix_ejob_conversation", "conversation_id"),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_EMBEDDING_JOB),
        primary_key=True,
        max_length=20,
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    seq_lo: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    seq_hi: int = Field(
        default=2**62,
        sa_column=Column(BigInteger, nullable=False, server_default=str(2**62)),
    )
    state: EmbeddingJobState = Field(default=EmbeddingJobState.pending, max_length=10)
    attempts: int = Field(default=0)
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    scheduled_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
