"""add mcp_connector_installs.discovery_metadata

Revision ID: 31dccfa1df5e
Revises: b9969dcb9d89
Create Date: 2026-05-18 13:32:22.953960

Add a JSON column for MCP discovery metadata (server icons +
websiteUrl + per-tool icons, captured from the MCP ``initialize``
handshake; spec rev 2025-11-25). Stored separately from
``tools_cache`` so citation editing — which reads ``input_schema`` /
``output_schema`` from ``tools_cache`` — stays decoupled.

Note: autogenerate also reports drops for the partial unique indexes
on ``mcp_connector_installs`` / ``mcp_credential_grants``. Those are
false positives: the model declares them migration-only because
SQLAlchemy's reflection of ``postgresql_where`` is inconsistent.
We intentionally omit them — see the docstring on
``cubeplex.models.mcp.MCPConnectorInstall``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "31dccfa1df5e"
down_revision: Union[str, Sequence[str], None] = "b9969dcb9d89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "mcp_connector_installs",
        sa.Column(
            "discovery_metadata",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("mcp_connector_installs", "discovery_metadata")
