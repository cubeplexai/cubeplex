"""Search index chunk — sliding window over a conversation's messages."""

from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_CONV_CHUNK, generate_public_id

VECTOR_DIM = 1024


class ConversationChunk(CubeboxBase, OrgScopedMixin, table=True):
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
    text: str
    embedding: Any = Field(
        sa_column=Column(Vector(VECTOR_DIM), nullable=False),
    )
    embed_model: str = Field(max_length=128)
