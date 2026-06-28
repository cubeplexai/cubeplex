"""fix sandbox entity dedup + rebuild index

Revision ID: ef2d2e6c9c7d
Revises: 66c3b3dfdec3
Create Date: 2026-06-28 12:54:48.588289

The previous migration (66c3b3dfdec3) created the `deleted_at` column and
tried to replace the unique index from `WHERE status IN ('provisioning',
'running')` to `WHERE deleted_at IS NULL`. The data cleanup only handled
`terminated` rows and dedicated-topic/conversation rows. But the old index
did not cover paused/pausing/resuming/failed/kill_pending — so duplicate
scope keys could exist in those statuses. This migration deduplicates ALL
remaining duplicates (keeping the newest row per scope key) and then creates
the index that the previous migration could not.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = 'ef2d2e6c9c7d'
down_revision: Union[str, Sequence[str], None] = '66c3b3dfdec3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Deduplicate: for each scope key with multiple live rows, keep the
    # newest one (by last_activity_at DESC, fallback created_at DESC),
    # soft-delete the rest. The old partial unique index only covered
    # (provisioning, running), so rows in other statuses (paused, failed,
    # kill_pending, pausing, resuming) could duplicate a running row.
    conn.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY org_id, workspace_id, scope_type, scope_id
                           ORDER BY COALESCE(last_activity_at, created_at) DESC
                       ) AS rn
                FROM user_sandboxes
                WHERE deleted_at IS NULL
            )
            UPDATE user_sandboxes
            SET deleted_at  = now(),
                status      = CASE
                    WHEN status IN ('provisioning','running','pausing','paused',
                                     'resuming','kill_pending')
                    THEN 'terminated'
                    ELSE status
                END,
                sandbox_id  = CASE WHEN rn > 1 THEN NULL ELSE sandbox_id END
            FROM ranked
            WHERE user_sandboxes.id = ranked.id AND ranked.rn > 1
            """
        )
    )

    # Now that duplicates are resolved, create the index the previous
    # migration could not. Idempotent: IF NOT EXISTS so this is safe on
    # databases where the original migration's create_index DID succeed.
    conn.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_sandbox_active_scope "
            "ON user_sandboxes (org_id, workspace_id, scope_type, scope_id) "
            "WHERE deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DROP INDEX IF EXISTS uq_user_sandbox_active_scope")
    )
