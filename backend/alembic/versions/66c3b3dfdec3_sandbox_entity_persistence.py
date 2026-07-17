"""sandbox entity persistence

Revision ID: 66c3b3dfdec3
Revises: 74a3486fa05d
Create Date: 2026-06-27 10:46:58.152049

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = '66c3b3dfdec3'
down_revision: Union[str, Sequence[str], None] = '74a3486fa05d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema changes (autogen)
    op.add_column('user_sandboxes', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('user_sandboxes', 'sandbox_id',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)

    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Data cleanup — MUST happen BEFORE the new index is created.
    # The old partial unique index only covered (provisioning, running).
    # Rows in other statuses (paused, failed, kill_pending, pausing,
    # resuming) could coexist with a provisioning/running row for the
    # same scope key, and the new index (WHERE deleted_at IS NULL)
    # will reject them.
    # ------------------------------------------------------------------

    # 1) Soft-delete historical terminated rows.
    conn.execute(
        sa.text(
            "UPDATE user_sandboxes SET deleted_at = updated_at "
            "WHERE status = 'terminated'"
        )
    )

    # 2) Soft-delete all dedicated-topic / group-chat rows.  The new
    #    model gives each its own scope-keyed PVC; existing rows get a
    #    fresh start (migration §3.3 step 3).
    conn.execute(
        sa.text(
            "UPDATE user_sandboxes SET deleted_at = now(), status = 'terminated', "
            "sandbox_id = NULL "
            "WHERE scope_type IN ('topic', 'conversation') AND deleted_at IS NULL"
        )
    )

    # 3) Deduplicate any remaining rows that share a scope key.  Per
    #    scope (org/ws/scope_type/scope_id) keep the *newest* row (by
    #    last_activity_at, falling back to created_at) and soft-delete
    #    the rest.
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

    # ------------------------------------------------------------------
    # Index swap — now safe because duplicates are resolved.
    # ------------------------------------------------------------------
    op.drop_index('uq_user_sandbox_active_scope', table_name='user_sandboxes')
    op.create_index(
        'uq_user_sandbox_active_scope',
        'user_sandboxes',
        ['org_id', 'workspace_id', 'scope_type', 'scope_id'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    # Schema rollback (autogen)
    op.alter_column('user_sandboxes', 'sandbox_id',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)
    op.drop_column('user_sandboxes', 'deleted_at')

    # Restore the old status-based partial index.  Postgres auto-drops
    # the `deleted_at IS NULL` index when the column is dropped above,
    # so only the CREATE is needed.
    op.create_index(
        'uq_user_sandbox_active_scope',
        'user_sandboxes',
        ['org_id', 'workspace_id', 'scope_type', 'scope_id'],
        unique=True,
        postgresql_where=sa.text(
            "status::text = ANY (ARRAY['provisioning'::character varying, "
            "'running'::character varying]::text[])"
        ),
    )
