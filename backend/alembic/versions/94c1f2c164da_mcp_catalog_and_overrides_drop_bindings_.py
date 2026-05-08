"""mcp_catalog and overrides; drop bindings; drop stdio

Revision ID: 94c1f2c164da
Revises: a2c0009ea3ad
Create Date: 2026-05-08 11:46:13.259438

This is the M2 schema breaking change:

- ``workspace_mcp_bindings`` is dropped (replaced by ``workspace_mcp_overrides``
  which only stores explicit *disable* rows; visibility is inherited by default).
- ``mcp_catalog_connectors`` is added as the system-level catalog of installable
  remote connectors.
- ``mcp_servers`` gains ``catalog_connector_id`` plus a partial unique index
  ``uq_mcp_install_per_catalog`` keyed by
  ``(org_id, COALESCE(owner_workspace_id, '_org'), catalog_connector_id)``.
- Any existing rows with ``transport='stdio'`` are deleted; the application
  layer no longer accepts stdio.

There is no compatibility migration; M2 was released with explicit
non-compat semantics — operators must reseed catalog connectors after
applying this revision.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "94c1f2c164da"
down_revision: Union[str, Sequence[str], None] = "b0def3f44ade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. Drop any pre-existing stdio rows (no compat).
    op.execute("DELETE FROM mcp_servers WHERE transport = 'stdio'")

    # --- 2. Add the new system-level catalog table.
    op.create_table(
        "mcp_catalog_connectors",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column(
            "description", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False
        ),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column(
            "server_url", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False
        ),
        sa.Column("transport", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("supported_auth_methods", sa.JSON(), nullable=False),
        sa.Column(
            "default_credential_scope",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column("oauth_dcr_supported", sa.Boolean(), nullable=True),
        sa.Column(
            "oauth_default_scope",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column(
            "oauth_static_client_id",
            sqlmodel.sql.sqltypes.AutoString(length=256),
            nullable=True,
        ),
        sa.Column(
            "oauth_static_client_secret_credential_id",
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=True,
        ),
        sa.Column("static_form_fields", sa.JSON(), nullable=True),
        sa.Column(
            "static_auth_header_template",
            sqlmodel.sql.sqltypes.AutoString(length=256),
            nullable=True,
        ),
        sa.Column(
            "cred_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["oauth_static_client_secret_credential_id"], ["credentials.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_mcp_catalog_slug"),
    )
    op.create_index(
        op.f("ix_mcp_catalog_connectors_slug"),
        "mcp_catalog_connectors",
        ["slug"],
        unique=False,
    )

    # --- 3. Add the per-workspace override table.
    op.create_table(
        "workspace_mcp_overrides",
        sa.Column("org_id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            "workspace_id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            "mcp_server_id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by_user_id",
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_override"),
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_mcp_server_id"),
        "workspace_mcp_overrides",
        ["mcp_server_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_org_id"),
        "workspace_mcp_overrides",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_workspace_id"),
        "workspace_mcp_overrides",
        ["workspace_id"],
        unique=False,
    )

    # --- 4. Drop the legacy bindings table.
    op.drop_index(
        op.f("ix_workspace_mcp_bindings_mcp_server_id"),
        table_name="workspace_mcp_bindings",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_bindings_org_id"), table_name="workspace_mcp_bindings"
    )
    op.drop_index(
        op.f("ix_workspace_mcp_bindings_workspace_id"),
        table_name="workspace_mcp_bindings",
    )
    op.drop_table("workspace_mcp_bindings")

    # --- 5. Wire mcp_servers → catalog connectors.
    op.add_column(
        "mcp_servers",
        sa.Column(
            "catalog_connector_id",
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_mcp_servers_catalog_connector_id"),
        "mcp_servers",
        ["catalog_connector_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_mcp_servers_catalog_connector_id",
        "mcp_servers",
        "mcp_catalog_connectors",
        ["catalog_connector_id"],
        ["id"],
    )

    # Partial unique index — at most one install per (org, owner_workspace, catalog).
    # ``COALESCE(owner_workspace_id, '_org')`` collapses NULL owner workspaces to a
    # constant so they collide; restricted to rows with a non-NULL catalog connector
    # so legacy custom installs can still coexist.
    op.execute(
        "CREATE UNIQUE INDEX uq_mcp_install_per_catalog "
        "ON mcp_servers (org_id, COALESCE(owner_workspace_id, '_org'), "
        "catalog_connector_id) "
        "WHERE catalog_connector_id IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS uq_mcp_install_per_catalog")
    op.drop_constraint(
        "fk_mcp_servers_catalog_connector_id", "mcp_servers", type_="foreignkey"
    )
    op.drop_index(op.f("ix_mcp_servers_catalog_connector_id"), table_name="mcp_servers")
    op.drop_column("mcp_servers", "catalog_connector_id")

    # Recreate workspace_mcp_bindings exactly as defined in
    # 1984c75dab8d_add_mcp_connector_tables.py: surrogate ``id VARCHAR(36)`` PK
    # plus a UniqueConstraint on (workspace_id, mcp_server_id). No FKs in the
    # original revision; later migrations are responsible for adding them.
    op.create_table(
        "workspace_mcp_bindings",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("org_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            "workspace_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column(
            "mcp_server_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sqlmodel.sql.sqltypes.AutoString(length=36),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_binding"),
    )
    op.create_index(
        op.f("ix_workspace_mcp_bindings_mcp_server_id"),
        "workspace_mcp_bindings",
        ["mcp_server_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_bindings_org_id"),
        "workspace_mcp_bindings",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_bindings_workspace_id"),
        "workspace_mcp_bindings",
        ["workspace_id"],
        unique=False,
    )

    op.drop_index(
        op.f("ix_workspace_mcp_overrides_workspace_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_overrides_org_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_index(
        op.f("ix_workspace_mcp_overrides_mcp_server_id"),
        table_name="workspace_mcp_overrides",
    )
    op.drop_table("workspace_mcp_overrides")

    op.drop_index(
        op.f("ix_mcp_catalog_connectors_slug"), table_name="mcp_catalog_connectors"
    )
    op.drop_table("mcp_catalog_connectors")
