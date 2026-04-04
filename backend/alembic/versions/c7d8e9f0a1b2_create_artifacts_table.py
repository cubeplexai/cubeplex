"""create_artifacts_table

Revision ID: c7d8e9f0a1b2
Revises: 88ca1a8071c4
Create Date: 2026-04-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "88ca1a8071c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column(
            "conversation_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("artifact_type", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=False),
        sa.Column("entry_file", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("mime_type", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_conversation_id", "artifacts", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_conversation_id", table_name="artifacts")
    op.drop_table("artifacts")
