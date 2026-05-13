"""conversations soft delete: add deleted_at

Revision ID: 2b4e5bebbc7f
Revises: 1991a15c011d
Create Date: 2026-05-13 20:06:35.591498

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b4e5bebbc7f'
down_revision: Union[str, Sequence[str], None] = '1991a15c011d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('conversations', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    # Partial index sized to the tiny minority of soft-deleted rows.
    # The hot read path filters via the existing ix_conversations_user_ws
    # index (selective by user+ws); a full index on deleted_at would be
    # ignored by the planner (>99% NULL) while costing every INSERT/UPDATE.
    op.create_index(
        'ix_conversations_deleted_at_partial',
        'conversations',
        ['deleted_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_conversations_deleted_at_partial', table_name='conversations')
    op.drop_column('conversations', 'deleted_at')
