"""m4_workspace_settings

Revision ID: bfd800fb39e1
Revises: d44dff875e38
Create Date: 2026-05-06 21:42:49.875958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bfd800fb39e1'
down_revision: Union[str, Sequence[str], None] = 'd44dff875e38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add workspace_id column to org_skill_installs
    op.add_column(
        "org_skill_installs",
        sa.Column("workspace_id", sa.String(20), sa.ForeignKey("workspaces.id"), nullable=True),
    )
    op.create_index("ix_osi_org_workspace", "org_skill_installs", ["org_id", "workspace_id"])

    # 2. Replace uq_org_skill_install with partial unique index (org-wide rows only)
    op.drop_constraint("uq_org_skill_install", "org_skill_installs", type_="unique")
    op.create_index(
        "uq_org_skill_install_org_wide",
        "org_skill_installs",
        ["org_id", "skill_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    # 3. Add unique constraint for workspace-private rows
    op.create_unique_constraint(
        "uq_org_skill_install_ws",
        "org_skill_installs",
        ["org_id", "workspace_id", "skill_id"],
    )

    # 4. Backfill AgentConfig for workspaces that don't have one
    # AgentConfig._PREFIX = "agt"; model_id has no default so provide empty string.
    from cubeplex.models.public_id import generate_public_id
    from sqlalchemy import text

    conn = op.get_bind()
    rows = conn.execute(
        text(
            """
            SELECT w.id, w.org_id FROM workspaces w
            WHERE NOT EXISTS (
                SELECT 1 FROM agent_configs ac WHERE ac.workspace_id = w.id
            )
            """
        )
    ).fetchall()
    for ws_id, org_id in rows:
        new_id = generate_public_id("agt")
        conn.execute(
            text(
                """
                INSERT INTO agent_configs
                    (id, org_id, workspace_id, system_prompt, model_id,
                     skill_ids, mcp_server_ids, created_at, updated_at)
                VALUES
                    (:id, :org_id, :ws_id, '', '', NULL, NULL, NOW(), NOW())
                """
            ),
            {"id": new_id, "org_id": org_id, "ws_id": ws_id},
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_org_skill_install_ws", "org_skill_installs", type_="unique")
    op.drop_index("uq_org_skill_install_org_wide", table_name="org_skill_installs")
    op.create_unique_constraint(
        "uq_org_skill_install", "org_skill_installs", ["org_id", "skill_id"]
    )
    op.drop_index("ix_osi_org_workspace", table_name="org_skill_installs")
    op.drop_column("org_skill_installs", "workspace_id")
