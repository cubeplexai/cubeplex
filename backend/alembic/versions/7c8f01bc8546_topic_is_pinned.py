"""topic_is_pinned

Revision ID: 7c8f01bc8546
Revises: b5242b73e9ce
Create Date: 2026-06-18 19:39:47.985761

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c8f01bc8546"
down_revision: Union[str, Sequence[str], None] = "b5242b73e9ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("topics", "is_pinned", server_default=None)


def downgrade() -> None:
    op.drop_column("topics", "is_pinned")
