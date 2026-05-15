"""add mcp four-layer tables

Revision ID: 3fcdfc800664
Revises: be0ae034a240
Create Date: 2026-05-16 03:04:07.265778

Adds the four-layer MCP connector schema (templates, installs,
workspace-connector states, credential grants) alongside the legacy
mcp_servers / mcp_catalog_connectors / workspace_mcp_overrides /
workspace_mcp_credentials / user_mcp_credentials tables. The legacy
tables stay in place until later migration tasks move every caller off
them; only then will a dedicated cleanup migration drop the dead
tables.

Partial unique indexes (postgresql_where) and CHECK constraints that
autogenerate cannot infer are added explicitly below.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (referenced by sqlmodel.sql.sqltypes.AutoString)

# revision identifiers, used by Alembic.
revision: str = '3fcdfc800664'
down_revision: Union[str, Sequence[str], None] = 'be0ae034a240'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the four new MCP tables, their partial unique indexes, and CHECKs."""

    # -- mcp_connector_templates -------------------------------------------------
    op.create_table(
        'mcp_connector_templates',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False),
        sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('server_url', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False),
        sa.Column('transport', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('supported_auth_methods', sa.JSON(), nullable=False),
        sa.Column(
            'default_credential_policy',
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column('oauth_dcr_supported', sa.Boolean(), nullable=True),
        sa.Column(
            'oauth_default_scope', sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True
        ),
        sa.Column(
            'oauth_static_client_id', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True
        ),
        sa.Column(
            'oauth_static_client_secret_credential_id',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=True,
        ),
        sa.Column('static_form_schema', sa.JSON(), nullable=True),
        sa.Column(
            'static_auth_header_template',
            sqlmodel.sql.sqltypes.AutoString(length=256),
            nullable=True,
        ),
        sa.Column(
            'template_metadata', sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            'tool_citation_defaults', sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ['oauth_static_client_secret_credential_id'], ['credentials.id']
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_mcp_connector_template_slug'),
    )
    op.create_index(
        op.f('ix_mcp_connector_templates_slug'),
        'mcp_connector_templates',
        ['slug'],
        unique=False,
    )

    # -- mcp_connector_installs --------------------------------------------------
    op.create_table(
        'mcp_connector_installs',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('org_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column('install_scope', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('template_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('server_url', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False),
        sa.Column(
            'server_url_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column('transport', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('auth_method', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column(
            'default_credential_policy',
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column('auth_status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column(
            'discovery_status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column(
            'install_state',
            sqlmodel.sql.sqltypes.AutoString(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            'oauth_client_config',
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column('headers', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('tools_cache', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('tool_citations', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            'last_error', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=True
        ),
        sa.Column('last_discovered_at', sa.DateTime(), nullable=True),
        sa.Column('timeout', sa.Float(), nullable=False),
        sa.Column('sse_read_timeout', sa.Float(), nullable=False),
        sa.Column(
            'auto_enroll_new_workspaces',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column(
            'created_by_user_id',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_method IN ('oauth','static','none')",
            name='ck_mcp_connector_installs_auth_method',
        ),
        sa.CheckConstraint(
            "install_scope IN ('org','workspace')",
            name='ck_mcp_connector_installs_scope',
        ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['template_id'], ['mcp_connector_templates.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_mcp_connector_installs_org_id'),
        'mcp_connector_installs',
        ['org_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_connector_installs_template_id'),
        'mcp_connector_installs',
        ['template_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_connector_installs_workspace_id'),
        'mcp_connector_installs',
        ['workspace_id'],
        unique=False,
    )

    # Partial unique indexes for installs (autogen cannot emit postgresql_where).
    # All install-scope indexes exclude install_state='uninstalled' so tombstoned
    # installs never block reinstalling the same template/URL/name.
    op.create_index(
        'uq_mcp_connector_install_url_org',
        'mcp_connector_installs',
        ['org_id', 'server_url_hash'],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL AND install_state = 'active'"),
    )
    op.create_index(
        'uq_mcp_connector_install_url_ws',
        'mcp_connector_installs',
        ['org_id', 'workspace_id', 'server_url_hash'],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL AND install_state = 'active'"),
    )
    op.create_index(
        'uq_mcp_connector_install_name_org',
        'mcp_connector_installs',
        ['org_id', 'name'],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL AND install_state = 'active'"),
    )
    op.create_index(
        'uq_mcp_connector_install_name_ws',
        'mcp_connector_installs',
        ['org_id', 'workspace_id', 'name'],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL AND install_state = 'active'"),
    )
    op.create_index(
        'uq_mcp_connector_install_per_template_org',
        'mcp_connector_installs',
        ['org_id', 'template_id'],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )
    op.create_index(
        'uq_mcp_connector_install_per_template_ws',
        'mcp_connector_installs',
        ['org_id', 'workspace_id', 'template_id'],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NOT NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )

    # -- mcp_workspace_connector_states ------------------------------------------
    op.create_table(
        'mcp_workspace_connector_states',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('org_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            'workspace_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False
        ),
        sa.Column('install_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            'enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False
        ),
        sa.Column(
            'credential_policy',
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column(
            'enablement_source',
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            'updated_by_user_id',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.CheckConstraint(
            "credential_policy IN ('org','workspace','user','none')",
            name='ck_mcp_workspace_connector_states_policy',
        ),
        sa.ForeignKeyConstraint(['install_id'], ['mcp_connector_installs.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'workspace_id', 'install_id', name='uq_mcp_workspace_connector_state'
        ),
    )
    op.create_index(
        op.f('ix_mcp_workspace_connector_states_install_id'),
        'mcp_workspace_connector_states',
        ['install_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_workspace_connector_states_org_id'),
        'mcp_workspace_connector_states',
        ['org_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_workspace_connector_states_workspace_id'),
        'mcp_workspace_connector_states',
        ['workspace_id'],
        unique=False,
    )

    # -- mcp_credential_grants ---------------------------------------------------
    op.create_table(
        'mcp_credential_grants',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('org_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('install_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            'grant_scope', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column(
            'credential_id', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False
        ),
        sa.Column(
            'refresh_credential_id',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=True,
        ),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column(
            'grant_status',
            sqlmodel.sql.sqltypes.AutoString(length=16),
            server_default=sa.text("'valid'"),
            nullable=False,
        ),
        sa.Column(
            'created_by_user_id',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(grant_scope='org' AND workspace_id IS NULL AND user_id IS NULL)"
            " OR (grant_scope='workspace' AND workspace_id IS NOT NULL AND user_id IS NULL)"
            " OR (grant_scope='user' AND workspace_id IS NOT NULL AND user_id IS NOT NULL)",
            name='ck_mcp_credential_grants_scope_columns',
        ),
        sa.CheckConstraint(
            "grant_scope IN ('org','workspace','user')",
            name='ck_mcp_credential_grants_scope',
        ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['credential_id'], ['credentials.id']),
        sa.ForeignKeyConstraint(['install_id'], ['mcp_connector_installs.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['refresh_credential_id'], ['credentials.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_mcp_credential_grants_install_id'),
        'mcp_credential_grants',
        ['install_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_credential_grants_org_id'),
        'mcp_credential_grants',
        ['org_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_credential_grants_user_id'),
        'mcp_credential_grants',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_mcp_credential_grants_workspace_id'),
        'mcp_credential_grants',
        ['workspace_id'],
        unique=False,
    )

    # Partial unique indexes for credential grants (one row per scope).
    op.create_index(
        'uq_mcp_credential_grant_org',
        'mcp_credential_grants',
        ['install_id'],
        unique=True,
        postgresql_where=sa.text("grant_scope = 'org'"),
    )
    op.create_index(
        'uq_mcp_credential_grant_workspace',
        'mcp_credential_grants',
        ['install_id', 'workspace_id'],
        unique=True,
        postgresql_where=sa.text("grant_scope = 'workspace'"),
    )
    op.create_index(
        'uq_mcp_credential_grant_user',
        'mcp_credential_grants',
        ['install_id', 'workspace_id', 'user_id'],
        unique=True,
        postgresql_where=sa.text("grant_scope = 'user'"),
    )


def downgrade() -> None:
    """Drop the four new tables. The legacy MCP tables are untouched."""

    # mcp_credential_grants
    op.drop_index(
        'uq_mcp_credential_grant_user',
        table_name='mcp_credential_grants',
        postgresql_where=sa.text("grant_scope = 'user'"),
    )
    op.drop_index(
        'uq_mcp_credential_grant_workspace',
        table_name='mcp_credential_grants',
        postgresql_where=sa.text("grant_scope = 'workspace'"),
    )
    op.drop_index(
        'uq_mcp_credential_grant_org',
        table_name='mcp_credential_grants',
        postgresql_where=sa.text("grant_scope = 'org'"),
    )
    op.drop_index(
        op.f('ix_mcp_credential_grants_workspace_id'),
        table_name='mcp_credential_grants',
    )
    op.drop_index(
        op.f('ix_mcp_credential_grants_user_id'), table_name='mcp_credential_grants'
    )
    op.drop_index(
        op.f('ix_mcp_credential_grants_org_id'), table_name='mcp_credential_grants'
    )
    op.drop_index(
        op.f('ix_mcp_credential_grants_install_id'), table_name='mcp_credential_grants'
    )
    op.drop_table('mcp_credential_grants')

    # mcp_workspace_connector_states
    op.drop_index(
        op.f('ix_mcp_workspace_connector_states_workspace_id'),
        table_name='mcp_workspace_connector_states',
    )
    op.drop_index(
        op.f('ix_mcp_workspace_connector_states_org_id'),
        table_name='mcp_workspace_connector_states',
    )
    op.drop_index(
        op.f('ix_mcp_workspace_connector_states_install_id'),
        table_name='mcp_workspace_connector_states',
    )
    op.drop_table('mcp_workspace_connector_states')

    # mcp_connector_installs
    op.drop_index(
        'uq_mcp_connector_install_per_template_ws',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text(
            "workspace_id IS NOT NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )
    op.drop_index(
        'uq_mcp_connector_install_per_template_org',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text(
            "workspace_id IS NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )
    op.drop_index(
        'uq_mcp_connector_install_name_ws',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text("workspace_id IS NOT NULL AND install_state = 'active'"),
    )
    op.drop_index(
        'uq_mcp_connector_install_name_org',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text("workspace_id IS NULL AND install_state = 'active'"),
    )
    op.drop_index(
        'uq_mcp_connector_install_url_ws',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text("workspace_id IS NOT NULL AND install_state = 'active'"),
    )
    op.drop_index(
        'uq_mcp_connector_install_url_org',
        table_name='mcp_connector_installs',
        postgresql_where=sa.text("workspace_id IS NULL AND install_state = 'active'"),
    )
    op.drop_index(
        op.f('ix_mcp_connector_installs_workspace_id'),
        table_name='mcp_connector_installs',
    )
    op.drop_index(
        op.f('ix_mcp_connector_installs_template_id'),
        table_name='mcp_connector_installs',
    )
    op.drop_index(
        op.f('ix_mcp_connector_installs_org_id'), table_name='mcp_connector_installs'
    )
    op.drop_table('mcp_connector_installs')

    # mcp_connector_templates
    op.drop_index(
        op.f('ix_mcp_connector_templates_slug'), table_name='mcp_connector_templates'
    )
    op.drop_table('mcp_connector_templates')
