"""drop legacy mcp tables

Drops the legacy MCP connector schema. The product has not shipped — there
is no released external contract to preserve. Tables dropped:

* ``mcp_servers``
* ``mcp_catalog_connectors``
* ``workspace_mcp_overrides``
* ``workspace_mcp_credentials``
* ``user_mcp_credentials``

The four-layer schema (``mcp_connector_templates`` /
``mcp_connector_installs`` / ``mcp_workspace_connector_states`` /
``mcp_credential_grants``) is untouched.

``downgrade`` recreates the five legacy tables with their indexes, FK
constraints, and partial unique indexes — the bodies were produced by
``alembic revision --autogenerate`` against the pre-cleanup schema and
verified to round-trip cleanly. The legacy SQLModels themselves are
deleted from the codebase, so a downgraded DB has empty tables that no
runtime code reads; restoring them only exists to let earlier migrations'
downgrade bodies (which do ``op.drop_column("mcp_servers", ...)`` etc.)
walk the chain without erroring.

Revision ID: b9969dcb9d89
Revises: 3fcdfc800664
Create Date: 2026-05-16 09:28:50.834456
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b9969dcb9d89"
down_revision: str | Sequence[str] | None = "3fcdfc800664"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the five legacy MCP tables.

    Drop order respects FK dependencies: ``mcp_catalog_connectors`` keeps
    children alive via ``mcp_servers.catalog_connector_id``, and
    ``mcp_servers`` keeps children alive via
    ``{workspace_mcp_overrides, workspace_mcp_credentials,
    user_mcp_credentials}.mcp_server_id``. Children drop first, parents last.
    """
    op.drop_index(
        op.f("ix_user_mcp_credentials_mcp_server_id"),
        table_name="user_mcp_credentials",
    )
    op.drop_index(op.f("ix_user_mcp_credentials_org_id"), table_name="user_mcp_credentials")
    op.drop_index(op.f("ix_user_mcp_credentials_user_id"), table_name="user_mcp_credentials")
    op.drop_table("user_mcp_credentials")

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
        op.f("ix_mcp_server_org_wide_name_unique"),
        table_name="mcp_servers",
        postgresql_where="(owner_workspace_id IS NULL)",
    )
    op.drop_index(
        op.f("ix_mcp_server_org_wide_url_unique"),
        table_name="mcp_servers",
        postgresql_where="(owner_workspace_id IS NULL)",
    )
    op.drop_index(op.f("ix_mcp_servers_catalog_connector_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_org_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_owner_workspace_id"), table_name="mcp_servers")
    op.drop_index(
        op.f("uq_mcp_install_per_catalog"),
        table_name="mcp_servers",
        postgresql_where="(catalog_connector_id IS NOT NULL)",
    )
    op.drop_table("mcp_servers")

    op.drop_index(op.f("ix_mcp_catalog_connectors_slug"), table_name="mcp_catalog_connectors")
    op.drop_table("mcp_catalog_connectors")


def downgrade() -> None:
    """Recreate the five legacy MCP tables (autogen output).

    Create order respects FK dependencies: parents
    (``mcp_catalog_connectors`` then ``mcp_servers``) first, then children.
    """
    op.create_table(
        "mcp_catalog_connectors",
        sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("slug", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("description", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("provider", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("server_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("transport", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "supported_auth_methods",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "default_credential_scope",
            sa.VARCHAR(length=16),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("oauth_dcr_supported", sa.BOOLEAN(), autoincrement=False, nullable=True),
        sa.Column("oauth_default_scope", sa.VARCHAR(length=512), autoincrement=False, nullable=True),
        sa.Column(
            "oauth_static_client_id",
            sa.VARCHAR(length=256),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "oauth_static_client_secret_credential_id",
            sa.VARCHAR(length=20),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "static_form_fields",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "static_auth_header_template",
            sa.VARCHAR(length=256),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "cred_metadata",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("status", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "tool_citations",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["oauth_static_client_secret_credential_id"],
            ["credentials.id"],
            name=op.f("mcp_catalog_connectors_oauth_static_client_secret_credenti_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("mcp_catalog_connectors_pkey")),
        sa.UniqueConstraint(
            "slug",
            name=op.f("uq_mcp_catalog_slug"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_mcp_catalog_connectors_slug"),
        "mcp_catalog_connectors",
        ["slug"],
        unique=False,
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("org_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "owner_workspace_id", sa.VARCHAR(length=20), autoincrement=False, nullable=True
        ),
        sa.Column("name", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("server_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column(
            "server_url_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=False
        ),
        sa.Column("transport", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column("auth_method", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "credential_scope", sa.VARCHAR(length=16), autoincrement=False, nullable=False
        ),
        sa.Column("credential_id", sa.VARCHAR(length=20), autoincrement=False, nullable=True),
        sa.Column(
            "oauth_client_config",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "headers",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "tools_cache",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("authed", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("last_error", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column(
            "last_discovered_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "timeout",
            sa.DOUBLE_PRECISION(precision=53),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "sse_read_timeout",
            sa.DOUBLE_PRECISION(precision=53),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column(
            "catalog_connector_id",
            sa.VARCHAR(length=20),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "tool_citations",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "auto_enroll_new_workspaces",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["catalog_connector_id"],
            ["mcp_catalog_connectors.id"],
            name=op.f("fk_mcp_servers_catalog_connector_id"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("mcp_servers_created_by_user_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("mcp_servers_credential_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("mcp_servers_org_id_fkey")
        ),
        sa.ForeignKeyConstraint(
            ["owner_workspace_id"],
            ["workspaces.id"],
            name=op.f("mcp_servers_owner_workspace_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("mcp_servers_pkey")),
        sa.UniqueConstraint(
            "org_id",
            "owner_workspace_id",
            "name",
            name=op.f("uq_mcp_server_name"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        sa.UniqueConstraint(
            "org_id",
            "owner_workspace_id",
            "server_url_hash",
            name=op.f("uq_mcp_server_url"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("uq_mcp_install_per_catalog"),
        "mcp_servers",
        ["org_id", sa.literal_column("COALESCE(owner_workspace_id, '_org'::character varying)"), "catalog_connector_id"],
        unique=True,
        postgresql_where="(catalog_connector_id IS NOT NULL)",
    )
    op.create_index(
        op.f("ix_mcp_servers_owner_workspace_id"),
        "mcp_servers",
        ["owner_workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_servers_org_id"), "mcp_servers", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_servers_catalog_connector_id"),
        "mcp_servers",
        ["catalog_connector_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_server_org_wide_url_unique"),
        "mcp_servers",
        ["org_id", "server_url_hash"],
        unique=True,
        postgresql_where="(owner_workspace_id IS NULL)",
    )
    op.create_index(
        op.f("ix_mcp_server_org_wide_name_unique"),
        "mcp_servers",
        ["org_id", "name"],
        unique=True,
        postgresql_where="(owner_workspace_id IS NULL)",
    )

    op.create_table(
        "workspace_mcp_credentials",
        sa.Column("id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("org_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "mcp_server_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column(
            "credential_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_by_user_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("workspace_mcp_credentials_created_by_user_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("workspace_mcp_credentials_credential_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["mcp_server_id"],
            ["mcp_servers.id"],
            name=op.f("workspace_mcp_credentials_mcp_server_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("workspace_mcp_credentials_org_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("workspace_mcp_credentials_workspace_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("workspace_mcp_credentials_pkey")),
        sa.UniqueConstraint(
            "workspace_id",
            "mcp_server_id",
            name=op.f("uq_ws_mcp_cred"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_workspace_mcp_credentials_workspace_id"),
        "workspace_mcp_credentials",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_credentials_org_id"),
        "workspace_mcp_credentials",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_credentials_mcp_server_id"),
        "workspace_mcp_credentials",
        ["mcp_server_id"],
        unique=False,
    )

    op.create_table(
        "workspace_mcp_overrides",
        sa.Column("org_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "mcp_server_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column("enabled", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column(
            "updated_by_user_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column("credential_mode", sa.VARCHAR(length=16), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(
            ["mcp_server_id"],
            ["mcp_servers.id"],
            name=op.f("workspace_mcp_overrides_mcp_server_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("workspace_mcp_overrides_org_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("workspace_mcp_overrides_updated_by_user_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("workspace_mcp_overrides_workspace_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("workspace_mcp_overrides_pkey")),
        sa.UniqueConstraint(
            "workspace_id",
            "mcp_server_id",
            name=op.f("uq_ws_mcp_override"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_workspace_id"),
        "workspace_mcp_overrides",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_org_id"),
        "workspace_mcp_overrides",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_mcp_overrides_mcp_server_id"),
        "workspace_mcp_overrides",
        ["mcp_server_id"],
        unique=False,
    )

    op.create_table(
        "user_mcp_credentials",
        sa.Column("id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("org_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "mcp_server_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column(
            "credential_id", sa.VARCHAR(length=20), autoincrement=False, nullable=False
        ),
        sa.Column(
            "oauth_refresh_token_credential_id",
            sa.VARCHAR(length=20),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "oauth_expires_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=True
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("user_mcp_credentials_credential_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["mcp_server_id"],
            ["mcp_servers.id"],
            name=op.f("user_mcp_credentials_mcp_server_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["oauth_refresh_token_credential_id"],
            ["credentials.id"],
            name=op.f("user_mcp_credentials_oauth_refresh_token_credential_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("user_mcp_credentials_org_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("user_mcp_credentials_user_id_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("user_mcp_credentials_pkey")),
        sa.UniqueConstraint(
            "user_id",
            "mcp_server_id",
            name=op.f("uq_user_mcp_cred"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_user_mcp_credentials_user_id"),
        "user_mcp_credentials",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_mcp_credentials_org_id"),
        "user_mcp_credentials",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_mcp_credentials_mcp_server_id"),
        "user_mcp_credentials",
        ["mcp_server_id"],
        unique=False,
    )
