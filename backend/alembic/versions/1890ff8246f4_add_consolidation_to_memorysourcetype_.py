"""add consolidation to memorysourcetype enum

Revision ID: 1890ff8246f4
Revises: 538af47f81eb
Create Date: 2026-05-25 14:05:24.610514

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1890ff8246f4'
down_revision: Union[str, Sequence[str], None] = '538af47f81eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the 'consolidation' value to the memorysourcetype Postgres enum.

    Background memory consolidation writes MemoryItem.source_type=CONSOLIDATION;
    without this value the first such insert fails with an invalid-enum error.
    Alembic autogenerate does NOT emit ALTER TYPE ... ADD VALUE, so this is
    hand-written. Run outside a transaction (autocommit) for cross-version safety.
    """
    # SQLAlchemy stores Python Enum members by NAME, so the existing labels are
    # the uppercase names (CONVERSATION, MANUAL, …) — add the NAME, not the
    # lowercase .value.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE memorysourcetype ADD VALUE IF NOT EXISTS 'CONSOLIDATION'")


def downgrade() -> None:
    """No-op: Postgres has no DROP VALUE for enums; removing a value safely would
    require recreating the type and rewriting every dependent column. Leave it."""
    pass
