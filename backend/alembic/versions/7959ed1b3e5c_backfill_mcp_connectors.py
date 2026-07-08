"""backfill mcp connectors

Revision ID: 7959ed1b3e5c
Revises: 7bcaf228a12e
Create Date: 2026-07-07 21:11:50.288538

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7959ed1b3e5c"
down_revision: str | Sequence[str] | None = "7bcaf228a12e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL_CONNECTORS_SQL = """
WITH active_installs AS (
    SELECT
        i.*,
        row_number() OVER (
            PARTITION BY i.org_id, COALESCE(i.template_id, ''), i.server_url_hash, i.slug_name
            ORDER BY (i.workspace_id IS NULL) DESC, i.created_at ASC, i.id ASC
        ) AS rn
    FROM mcp_connector_installs i
    WHERE i.install_state = 'active'
),
canonical_installs AS (
    SELECT * FROM active_installs WHERE rn = 1
)
INSERT INTO mcp_connectors (
    id,
    created_at,
    updated_at,
    org_id,
    template_id,
    name,
    slug_name,
    server_url,
    server_url_hash,
    transport,
    auth_method,
    oauth_client_config,
    static_auth_style,
    static_auth_header_name,
    static_auth_query_param,
    tools_cache,
    tool_citations,
    discovery_status,
    last_error,
    status,
    created_by_user_id
)
SELECT
    'mcpco-' || substr(
        md5(
            ci.org_id || ':' ||
            COALESCE(ci.template_id, '') || ':' ||
            ci.server_url_hash || ':' ||
            ci.slug_name
        ),
        1,
        14
    ),
    ci.created_at,
    now(),
    ci.org_id,
    ci.template_id,
    ci.name,
    ci.slug_name,
    ci.server_url,
    ci.server_url_hash,
    ci.transport,
    ci.auth_method,
    ci.oauth_client_config,
    ci.static_auth_style,
    ci.static_auth_header_name,
    ci.static_auth_query_param,
    ci.tools_cache,
    ci.tool_citations,
    ci.discovery_status,
    ci.last_error,
    'active',
    ci.created_by_user_id
FROM canonical_installs ci
ON CONFLICT DO NOTHING
"""


INSTALL_CONNECTORS_CTE = """
WITH install_connectors AS (
    SELECT install_id, connector_id
    FROM (
        SELECT
            i.id AS install_id,
            c.id AS connector_id,
            row_number() OVER (
                PARTITION BY i.id
                ORDER BY
                    (c.template_id IS NOT DISTINCT FROM i.template_id) DESC,
                    (c.server_url_hash = i.server_url_hash) DESC,
                    (c.slug_name = i.slug_name) DESC,
                    c.created_at ASC,
                    c.id ASC
            ) AS rn
        FROM mcp_connector_installs i
        JOIN mcp_connectors c
            ON c.org_id = i.org_id
            AND c.status = 'active'
            AND (
                c.template_id IS NOT DISTINCT FROM i.template_id
                OR c.server_url_hash = i.server_url_hash
                OR c.slug_name = i.slug_name
            )
        WHERE i.install_state = 'active'
    ) ranked
    WHERE rn = 1
)
"""


BACKFILL_STATES_SQL = (
    INSTALL_CONNECTORS_CTE
    + """
UPDATE mcp_workspace_connector_states s
SET connector_id = ic.connector_id,
    updated_at = now()
FROM install_connectors ic
WHERE s.install_id = ic.install_id
  AND s.connector_id IS NULL
"""
)


CREATE_WORKSPACE_STATES_SQL = (
    INSTALL_CONNECTORS_CTE
    + """
INSERT INTO mcp_workspace_connector_states (
    id,
    created_at,
    updated_at,
    org_id,
    workspace_id,
    install_id,
    connector_id,
    enabled,
    credential_policy,
    enablement_source,
    updated_by_user_id
)
SELECT
    'mcwcs-' || substr(md5('workspace-state:' || i.id), 1, 14),
    i.created_at,
    now(),
    i.org_id,
    i.workspace_id,
    i.id,
    ic.connector_id,
    true,
    i.default_credential_policy,
    'workspace_install',
    i.created_by_user_id
FROM mcp_connector_installs i
JOIN install_connectors ic ON ic.install_id = i.id
WHERE i.install_state = 'active'
  AND i.workspace_id IS NOT NULL
ON CONFLICT ON CONSTRAINT uq_mcp_workspace_connector_state
DO UPDATE SET connector_id = EXCLUDED.connector_id,
              credential_policy = EXCLUDED.credential_policy,
              updated_at = now()
"""
)


BACKFILL_GRANTS_SQL = (
    INSTALL_CONNECTORS_CTE
    + """
UPDATE mcp_credential_grants g
SET connector_id = ic.connector_id,
    updated_at = now()
FROM install_connectors ic
WHERE g.install_id = ic.install_id
  AND g.connector_id IS NULL
"""
)


TOMBSTONE_WORKSPACE_INSTALLS_SQL = """
UPDATE mcp_connector_installs
SET install_state = 'uninstalled',
    updated_at = now()
WHERE install_state = 'active'
  AND workspace_id IS NOT NULL
"""


def upgrade() -> None:
    """Backfill legacy install rows into connector identity rows."""
    op.execute(BACKFILL_CONNECTORS_SQL)
    op.execute(BACKFILL_STATES_SQL)
    op.execute(CREATE_WORKSPACE_STATES_SQL)
    op.execute(BACKFILL_GRANTS_SQL)
    op.execute(TOMBSTONE_WORKSPACE_INSTALLS_SQL)


def downgrade() -> None:
    """Remove connector backfill data and restore legacy workspace installs best-effort."""
    op.execute(
        """
        UPDATE mcp_connector_installs i
        SET install_state = 'active',
            updated_at = now()
        FROM mcp_workspace_connector_states s
        WHERE s.install_id = i.id
          AND s.connector_id IS NOT NULL
          AND i.workspace_id IS NOT NULL
          AND i.install_state = 'uninstalled'
        """
    )
    op.execute(
        "UPDATE mcp_credential_grants SET connector_id = NULL WHERE connector_id IS NOT NULL"
    )
    op.execute(
        """
        UPDATE mcp_workspace_connector_states
        SET connector_id = NULL,
            updated_at = now()
        WHERE connector_id IS NOT NULL
        """
    )
    op.execute("DELETE FROM mcp_connectors")
