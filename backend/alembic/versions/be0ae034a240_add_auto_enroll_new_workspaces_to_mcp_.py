"""add auto_enroll_new_workspaces to mcp_servers

Revision ID: be0ae034a240
Revises: 94630a9e13b4
Create Date: 2026-05-15 15:17:08.740842

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'be0ae034a240'
down_revision: Union[str, Sequence[str], None] = '94630a9e13b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "auto_enroll_new_workspaces",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "auto_enroll_new_workspaces")
