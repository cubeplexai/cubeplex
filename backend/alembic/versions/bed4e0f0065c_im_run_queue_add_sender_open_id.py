"""im_run_queue add sender_open_id

Revision ID: bed4e0f0065c
Revises: 9ccd63170399
Create Date: 2026-06-14 02:22:10.120904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (referenced by sqlmodel.sql.sqltypes.AutoString)


# revision identifiers, used by Alembic.
revision: str = 'bed4e0f0065c'
down_revision: Union[str, Sequence[str], None] = '9ccd63170399'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'im_run_queue',
        sa.Column('sender_open_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('im_run_queue', 'sender_open_id')
