"""Search index chunk — sliding window over a conversation's messages."""

from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, Index, Text
from sqlmodel import Field

from cubeplex.config import config
from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin
from cubeplex.models.public_id import PREFIX_CONV_CHUNK, generate_public_id

# Read at import time so the SQLAlchemy column is built with the operator's
# chosen dim. Operators who want a different dim set search.embedding.vector_dim
# in config and run the migration on a fresh schema; see the startup three-way
# check (cubeplex.services.conversation_search.startup) for drift detection.
VECTOR_DIM = int(config.get("search.embedding.vector_dim", 1024))


class ConversationChunk(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_CONV_CHUNK
    __tablename__ = "conversation_chunks"
    __table_args__ = (
        Index("ix_chunks_scope", "org_id", "workspace_id", "creator_user_id"),
        Index("ix_chunks_conversation", "conversation_id", "chunk_seq", unique=True),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_CONV_CHUNK),
        primary_key=True,
        max_length=20,
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    chunk_seq: int = Field(ge=0)
    seq_lo: int = Field(sa_column=Column(BigInteger, nullable=False))
    seq_hi: int = Field(sa_column=Column(BigInteger, nullable=False))
    text: str = Field(sa_column=Column(Text, nullable=False))
    # Nullable: when no embedding provider is configured the worker writes
    # chunks with embedding=NULL so the lexical leg still has rows to query.
    embedding: Any | None = Field(
        default=None,
        sa_column=Column(Vector(VECTOR_DIM), nullable=True),
    )
    embed_model: str = Field(max_length=128)
