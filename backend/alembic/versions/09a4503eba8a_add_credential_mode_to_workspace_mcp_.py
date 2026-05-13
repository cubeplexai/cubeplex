"""add credential_mode to workspace_mcp_overrides

Revision ID: 09a4503eba8a
Revises: 4be6ed892e02
Create Date: 2026-05-12 23:07:37.850911

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09a4503eba8a'
down_revision: Union[str, Sequence[str], None] = '4be6ed892e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'workspace_mcp_overrides',
        sa.Column(
            'credential_mode',
            sa.String(length=16),
            nullable=False,
            server_default='org',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workspace_mcp_overrides', 'credential_mode')
