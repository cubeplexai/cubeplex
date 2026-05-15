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

This migration is **not reversible** — ``downgrade`` raises.

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
    """Drop the five legacy MCP tables.

    Order matters: FK dependencies require child tables (``user_mcp_credentials``,
    ``workspace_mcp_credentials``, ``workspace_mcp_overrides``) to drop before
    their parent (``mcp_servers``); ``mcp_servers`` references
    ``mcp_catalog_connectors`` via ``catalog_connector_id`` so the catalog
    table must drop last.
    """
    op.drop_index(
        op.f("ix_workspace_mcp_overrides_mcp_server_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_overrides_org_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_overrides_workspace_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_table("workspace_mcp_overrides")

    op.drop_index(
        op.f("ix_workspace_mcp_credentials_mcp_server_id"),
        table_name="workspace_mcp_credentials",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_credentials_org_id"),
        table_name="workspace_mcp_credentials",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_credentials_workspace_id"),
        table_name="workspace_mcp_credentials",
    )
    op.drop_table("workspace_mcp_credentials")

    op.drop_index(
        op.f("ix_user_mcp_credentials_mcp_server_id"),
        table_name="user_mcp_credentials",
    )
    op.drop_index(
        op.f("ix_user_mcp_credentials_org_id"),
        table_name="user_mcp_credentials",
    )
    op.drop_index(
        op.f("ix_user_mcp_credentials_user_id"),
        table_name="user_mcp_credentials",
    )
    op.drop_table("user_mcp_credentials")

    op.drop_index(
        op.f("ix_mcp_server_org_wide_name_unique"),
        table_name="mcp_servers",
        postgresql_where="(owner_workspace_id IS NULL)",
    )
    op.drop_index(
        op.f("ix_mcp_server_org_wide_url_unique"),
        table_name="mcp_servers",
        postgresql_where="(owner_workspace_id IS NULL)",
    )
    op.drop_index(
        op.f("ix_mcp_servers_catalog_connector_id"),
        table_name="mcp_servers",
    )
    op.drop_index(op.f("ix_mcp_servers_org_id"), table_name="mcp_servers")
    op.drop_index(
        op.f("ix_mcp_servers_owner_workspace_id"),
        table_name="mcp_servers",
    )
    op.drop_index(
        op.f("uq_mcp_install_per_catalog"),
        table_name="mcp_servers",
        postgresql_where="(catalog_connector_id IS NOT NULL)",
    )
    op.drop_table("mcp_servers")

    op.drop_index(
        op.f("ix_mcp_catalog_connectors_slug"),
        table_name="mcp_catalog_connectors",
    )
    op.drop_table("mcp_catalog_connectors")


def downgrade() -> None:
    """Destructive legacy cleanup is not reversible."""
    raise RuntimeError("Destructive legacy MCP cleanup is not reversible")
