"""add_sso_last_idp_attributes

Revision ID: 59fd2d03ce79
Revises: f1a53d345417
Create Date: 2026-06-18 18:40:18.291598

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '59fd2d03ce79'
down_revision: Union[str, Sequence[str], None] = 'f1a53d345417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sso_connections', sa.Column('last_idp_attributes', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('sso_connections', 'last_idp_attributes')
