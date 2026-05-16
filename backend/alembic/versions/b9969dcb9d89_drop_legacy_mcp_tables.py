"""drop legacy mcp tables

Removes the legacy MCP connector schema from the database. The product
has not shipped — no forward-compat needed. Tables dropped:

* ``mcp_servers``
* ``mcp_catalog_connectors``
* ``workspace_mcp_overrides``
* ``workspace_mcp_credentials``
* ``user_mcp_credentials``

The four-layer schema (``mcp_connector_templates`` /
``mcp_connector_installs`` / ``mcp_workspace_connector_states`` /
``mcp_credential_grants``) is untouched.

``downgrade`` is a no-op so the alembic version pointer can step back
without erroring (lets ``alembic downgrade -1`` and branch-switch
auto-downgrade work). It does NOT recreate the legacy tables — the
legacy SQLModels are gone, no code reads them, and there is no shipped
data to migrate. For a full reset, use
``scripts/worktree-env reseed-db`` (drops the database, recreates it,
re-runs ``alembic upgrade head`` from base — never walks downgrade).

Revision ID: b9969dcb9d89
Revises: 3fcdfc800664
Create Date: 2026-05-16 07:27:49.235109
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b9969dcb9d89"
down_revision: str | Sequence[str] | None = "3fcdfc800664"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the five legacy MCP tables, idempotently.

    Uses ``DROP TABLE IF EXISTS ... CASCADE`` so re-running upgrade after a
    downgrade-then-upgrade roundtrip is a no-op rather than an error. CASCADE
    also drops the table's own indexes and any dependent FK constraints in one
    statement, so we don't need to list each index by name.

    Order matters for FK dependencies: child tables (``user_mcp_credentials``,
    ``workspace_mcp_credentials``, ``workspace_mcp_overrides``) drop before
    their parent ``mcp_servers``; ``mcp_servers`` references
    ``mcp_catalog_connectors`` via ``catalog_connector_id`` so the catalog
    table drops last. ``CASCADE`` makes order tolerant but we keep it
    deliberate for clarity.
    """
    for table in (
        "workspace_mcp_overrides",
        "workspace_mcp_credentials",
        "user_mcp_credentials",
        "mcp_servers",
        "mcp_catalog_connectors",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    """No-op: pointer moves back, schema unchanged.

    The legacy MCP tables are not recreated. See module docstring.
    """
    pass
