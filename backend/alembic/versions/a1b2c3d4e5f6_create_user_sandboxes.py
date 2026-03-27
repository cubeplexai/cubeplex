"""create_user_sandboxes

Revision ID: a1b2c3d4e5f6
Revises: b744f6318cd3
Create Date: 2026-03-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'b744f6318cd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('user_sandboxes',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('sandbox_id', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('image', sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False),
        sa.Column('volumes_config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(), nullable=False),
        sa.Column('ttl_seconds', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sandbox_id'),
    )
    op.create_index('ix_user_sandboxes_user_id', 'user_sandboxes', ['user_id'], unique=False)
    op.create_index(
        'ix_user_sandboxes_user_status', 'user_sandboxes', ['user_id', 'status'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_user_sandboxes_user_status', table_name='user_sandboxes')
    op.drop_index('ix_user_sandboxes_user_id', table_name='user_sandboxes')
    op.drop_table('user_sandboxes')
