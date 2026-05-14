"""add tool_citations to mcp tables

Revision ID: 94630a9e13b4
Revises: 555c11215b57
Create Date: 2026-05-15 00:30:11.413495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '94630a9e13b4'
down_revision: Union[str, Sequence[str], None] = '555c11215b57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_catalog_connectors",
        sa.Column(
            "tool_citations",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "mcp_servers",
        sa.Column(
            "tool_citations",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "tool_citations")
    op.drop_column("mcp_catalog_connectors", "tool_citations")
