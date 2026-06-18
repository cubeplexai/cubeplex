"""add user avatar_url

Revision ID: f1a53d345417
Revises: ff70bc6d4f4f
Create Date: 2026-06-18 18:26:39.668701

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'f1a53d345417'
down_revision: Union[str, Sequence[str], None] = 'ff70bc6d4f4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('avatar_url', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'avatar_url')
