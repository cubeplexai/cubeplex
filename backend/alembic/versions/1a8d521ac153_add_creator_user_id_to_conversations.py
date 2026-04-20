"""add creator_user_id to conversations

Revision ID: 1a8d521ac153
Revises: 1d1dab71f0fa
Create Date: 2026-04-20 17:51:31.505436

Backfills existing rows from the workspace's admin membership (option 1
from the design discussion). Fails the migration if any workspace has
conversations but no admin — the operator must intervene.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1a8d521ac153"
down_revision: str | Sequence[str] | None = "1d1dab71f0fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add column as nullable so backfill can populate it.
    op.add_column(
        "conversations",
        sa.Column(
            "creator_user_id",
            sqlmodel.sql.sqltypes.AutoString(length=36),
            nullable=True,
        ),
    )

    # 2. Backfill from workspace admin membership. Picks one admin
    # arbitrarily when a workspace has multiple — correct for the common
    # "Personal" workspace (single admin = creator), best-effort for
    # multi-admin workspaces.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE conversations c
            JOIN (
                SELECT workspace_id, MIN(user_id) AS user_id
                FROM memberships
                WHERE role = 'admin'
                GROUP BY workspace_id
            ) m ON m.workspace_id = c.workspace_id
            SET c.creator_user_id = m.user_id
            WHERE c.creator_user_id IS NULL
            """
        )
    )

    # 3. Verify no rows were left unbackfilled.
    remaining = bind.execute(
        sa.text("SELECT COUNT(*) FROM conversations WHERE creator_user_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{remaining} conversation row(s) could not be backfilled "
            "(workspace has no admin). Resolve manually before re-running."
        )

    # 4. Enforce NOT NULL now that every row is populated.
    op.alter_column(
        "conversations",
        "creator_user_id",
        existing_type=sqlmodel.sql.sqltypes.AutoString(length=36),
        nullable=False,
    )

    # 5. Swap indexes.
    op.drop_index(op.f("ix_conversations_org_ws"), table_name="conversations")
    op.create_index(
        "ix_conversations_user_ws",
        "conversations",
        ["creator_user_id", "workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_conversations_user_ws", table_name="conversations")
    op.create_index(
        op.f("ix_conversations_org_ws"),
        "conversations",
        ["org_id", "workspace_id"],
        unique=False,
    )
    op.drop_column("conversations", "creator_user_id")
