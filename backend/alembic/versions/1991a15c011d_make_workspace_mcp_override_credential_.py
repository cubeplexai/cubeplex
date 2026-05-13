"""make workspace mcp override credential_mode nullable

Revision ID: 1991a15c011d
Revises: 09a4503eba8a
Create Date: 2026-05-13 11:52:40.136741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1991a15c011d'
down_revision: Union[str, Sequence[str], None] = '09a4503eba8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    ``credential_mode`` originally landed as NOT NULL with server_default='org',
    which made every pre-existing override row claim a hard ``org`` mode even
    when the workspace had never opted out of the server-level ``credential_scope``.
    That broke user-scope OAuth installs: the runtime would resolve effective_mode
    to 'org', look up a non-existent org credential, and call the upstream MCP
    with no Authorization header.

    Make the column nullable (NULL = inherit ``MCPServer.credential_scope``) and
    rewrite the migration-backfilled 'org' rows back to NULL. We can't tell apart
    user-set 'org' from migration-backfilled 'org' for rows created before this
    revision, but the UI hasn't shipped an explicit 'org' opt-in yet, so treating
    them all as inherit is safe.
    """
    op.execute("UPDATE workspace_mcp_overrides SET credential_mode = NULL WHERE credential_mode = 'org'")
    op.alter_column(
        "workspace_mcp_overrides",
        "credential_mode",
        existing_type=sa.String(length=16),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE workspace_mcp_overrides SET credential_mode = 'org' WHERE credential_mode IS NULL")
    op.alter_column(
        "workspace_mcp_overrides",
        "credential_mode",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="org",
    )
