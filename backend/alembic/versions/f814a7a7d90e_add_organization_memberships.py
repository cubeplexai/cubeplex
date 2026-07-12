"""add organization_memberships

Revision ID: f814a7a7d90e
Revises: a2c0009ea3ad
Create Date: 2026-05-07 17:47:13.094791

Backfill rule:
- Owner per existing org = the user with the earliest workspace-membership
  created_at in any workspace of that org.
- Every other user with a workspace membership in the org = member.
- Workspace-admin status is NOT carried over to org admin; promote those
  users via `cubeplex admin grant-admin <email>` after migration if needed.

NOTE for operators on populated dev DBs: the "earliest workspace-membership"
heuristic may pick an unexpected owner. Use `cubeplex admin grant-admin /
revoke-admin` to reconcile, or hand-edit organization_memberships.role
before relying on org-level admin checks (Task 4 onwards reads this row
to gate /admin and /admin/me).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f814a7a7d90e'
down_revision: Union[str, Sequence[str], None] = 'a2c0009ea3ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_memberships",
        sa.Column("user_id", sa.String(length=20), nullable=False),
        sa.Column("org_id", sa.String(length=20), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "org_id"),
    )
    op.create_index(
        "ix_org_memberships_org_id",
        "organization_memberships",
        ["org_id"],
    )
    op.create_index(
        "uq_org_membership_owner",
        "organization_memberships",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )

    # Backfill: owner = earliest workspace-membership creator per org
    op.execute(
        """
        INSERT INTO organization_memberships (user_id, org_id, role, created_at, updated_at)
        SELECT DISTINCT ON (w.org_id)
               m.user_id, w.org_id, 'owner', NOW(), NOW()
        FROM memberships m
        JOIN workspaces w ON w.id = m.workspace_id
        ORDER BY w.org_id, m.created_at ASC
        """
    )
    # Backfill: member = anyone else with a workspace membership in that org
    op.execute(
        """
        INSERT INTO organization_memberships (user_id, org_id, role, created_at, updated_at)
        SELECT DISTINCT m.user_id, w.org_id, 'member', NOW(), NOW()
        FROM memberships m
        JOIN workspaces w ON w.id = m.workspace_id
        LEFT JOIN organization_memberships om
               ON om.user_id = m.user_id AND om.org_id = w.org_id
        WHERE om.user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("uq_org_membership_owner", table_name="organization_memberships")
    op.drop_index("ix_org_memberships_org_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")
