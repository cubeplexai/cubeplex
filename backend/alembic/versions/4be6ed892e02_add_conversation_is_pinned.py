"""add conversation is_pinned

Revision ID: 4be6ed892e02
Revises: 94c1f2c164da
Create Date: 2026-05-12 18:48:08.425250

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4be6ed892e02'
down_revision: Union[str, Sequence[str], None] = '94c1f2c164da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversations',
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('conversations', 'is_pinned')
