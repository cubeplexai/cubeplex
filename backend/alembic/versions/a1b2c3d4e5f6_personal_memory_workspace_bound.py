"""personal memory workspace-bound scope

Revision ID: a1b2c3d4e5f6
Revises: 0475a231f07f
Create Date: 2026-07-26 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "0475a231f07f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Allow personal rows to carry workspace_id (and keep org_id NULL).
    # Orphans may still have workspace_id NULL after backfill; app excludes them
    # from injection. New writes always set workspace_id via MemoryService.
    op.drop_constraint("ck_memory_scope_targets", "memory_items", type_="check")
    op.create_check_constraint(
        "ck_memory_scope_targets",
        "memory_items",
        "(scope = 'PERSONAL' AND owner_user_id IS NOT NULL AND org_id IS NULL) "
        "OR (scope = 'WORKSPACE' AND workspace_id IS NOT NULL "
        "AND org_id IS NOT NULL AND owner_user_id IS NULL) "
        "OR (scope = 'ORG' AND org_id IS NOT NULL "
        "AND workspace_id IS NULL AND owner_user_id IS NULL)",
    )

    op.drop_index("ix_memory_personal", table_name="memory_items")
    op.create_index(
        "ix_memory_personal",
        "memory_items",
        ["scope", "owner_user_id", "workspace_id"],
        unique=False,
    )

    # Backfill personal.workspace_id from source conversation when possible.
    op.execute(
        """
        UPDATE memory_items AS mi
        SET workspace_id = c.workspace_id
        FROM conversations AS c
        WHERE mi.source_conversation_id = c.id
          AND mi.scope = 'PERSONAL'
          AND mi.workspace_id IS NULL
          AND c.workspace_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Clear workspace_id on personal so the old constraint can be reapplied.
    op.execute(
        """
        UPDATE memory_items
        SET workspace_id = NULL
        WHERE scope = 'PERSONAL'
        """
    )

    op.drop_index("ix_memory_personal", table_name="memory_items")
    op.create_index(
        "ix_memory_personal",
        "memory_items",
        ["scope", "owner_user_id"],
        unique=False,
    )

    op.drop_constraint("ck_memory_scope_targets", "memory_items", type_="check")
    op.create_check_constraint(
        "ck_memory_scope_targets",
        "memory_items",
        "(scope = 'PERSONAL' AND owner_user_id IS NOT NULL "
        "AND org_id IS NULL AND workspace_id IS NULL) "
        "OR (scope = 'WORKSPACE' AND workspace_id IS NOT NULL "
        "AND org_id IS NOT NULL AND owner_user_id IS NULL) "
        "OR (scope = 'ORG' AND org_id IS NOT NULL "
        "AND workspace_id IS NULL AND owner_user_id IS NULL)",
    )
