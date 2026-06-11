"""conversation_search_tables

Revision ID: fabe1279b9f6
Revises: ab40489adff0
Create Date: 2026-06-12 01:27:03.126207
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "fabe1279b9f6"
down_revision: str | None = "ab40489adff0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at migration-author time. Migrations are immutable assets — one
# revision must always emit the same DDL. Switching backend or dimension
# post-deploy requires a NEW revision (drop+create), not editing this one.
LEXICAL_BACKEND = "pgroonga"
VECTOR_DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if LEXICAL_BACKEND == "pgroonga":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgroonga")
    elif LEXICAL_BACKEND == "pg_bigm":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_bigm")
    else:
        raise RuntimeError(f"Unknown lexical backend: {LEXICAL_BACKEND}")

    op.create_table(
        "conversation_chunks",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=20),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(length=20),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "creator_user_id",
            sa.String(length=20),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=20),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("chunk_seq", sa.Integer, nullable=False),
        sa.Column("seq_lo", sa.BigInteger, nullable=False),
        sa.Column("seq_hi", sa.BigInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(VECTOR_DIM), nullable=False),
        sa.Column("embed_model", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chunks_scope",
        "conversation_chunks",
        ["org_id", "workspace_id", "creator_user_id"],
    )
    op.create_index(
        "ix_chunks_conversation",
        "conversation_chunks",
        ["conversation_id", "chunk_seq"],
        unique=True,
    )
    # HNSW vector index (pgvector ≥ 0.5)
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw "
        "ON conversation_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    # Lexical index — DDL depends on backend
    if LEXICAL_BACKEND == "pgroonga":
        op.execute(
            "CREATE INDEX ix_chunks_text_lexical "
            "ON conversation_chunks USING pgroonga (text)"
        )
    else:
        op.execute(
            "CREATE INDEX ix_chunks_text_lexical ON conversation_chunks "
            "USING gin (text gin_bigm_ops)"
        )

    op.create_table(
        "embedding_jobs",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=20),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(length=20),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "creator_user_id",
            sa.String(length=20),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=20),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("seq_lo", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("seq_hi", sa.BigInteger, nullable=False, server_default=str(2**62)),
        sa.Column("state", sa.String(length=10), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Index covers the claim query `WHERE state='pending' AND scheduled_at
    # <= now() ORDER BY scheduled_at FOR UPDATE SKIP LOCKED`. Indexing on
    # created_at instead would force the planner into a heap scan + sort.
    op.create_index("ix_ejob_pending", "embedding_jobs", ["state", "scheduled_at"])
    op.create_index("ix_ejob_conversation", "embedding_jobs", ["conversation_id"])

    op.create_table(
        "search_backfill_progress",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=20),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(length=20),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column("last_conversation_id", sa.String(length=20), nullable=True),
        sa.Column("enqueued_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_sbp_ws", "search_backfill_progress", ["workspace_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("search_backfill_progress")
    op.drop_table("embedding_jobs")
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_lexical")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_table("conversation_chunks")
    # Extensions left in place — other features may use them.
