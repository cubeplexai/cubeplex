"""mcp install uniqueness across scopes (R1 R2 R3)

Revision ID: 47105505d677
Revises: 31dccfa1df5e
Create Date: 2026-05-18 16:48:07.993236

Replace the 6 per-scope partial unique indexes on
``mcp_connector_installs`` with 3 org-wide partial unique indexes so
an org cannot host two active installs with the same display name,
server URL, or template — regardless of whether one is org-scope and
the other is workspace-scope. Same-scope duplicates were already
forbidden; this closes the cross-scope hole that let the LLM-runtime
tool namespace slap an ugly ``_<install-id-tail>`` collision suffix
onto the second install's tools.

The model docstring on ``MCPConnectorInstall.__table_args__`` notes
that these partial indexes are migration-only — SQLAlchemy
autogenerate can't round-trip ``postgresql_where`` reliably across
versions. We hand-write the new state to keep the migration the source
of truth.

Existing duplicates **abort the upgrade** with a message listing the
conflicting rows. Operators must clean the data (uninstall one of each
duplicate group) before re-running. This matches option B in the
brainstorm — automatic data cleanup risks killing the wrong row, and
"which one is the canonical install" is the operator's call.

Autogenerate also flagged the three ``uq_mcp_credential_grant_*``
partial indexes as removed. That is a false positive (same reflection
limitation as the install ones) — those indexes stay; we do not drop
them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "47105505d677"
down_revision: Union[str, Sequence[str], None] = "31dccfa1df5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Each entry: ``(label, column expression for GROUP BY, extra WHERE clause)``.
# The duplicate-detection query groups active installs per org by each
# column and reports any group of size > 1.
_DUPLICATE_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("name", "name", ""),
    ("server_url_hash", "server_url_hash", ""),
    ("template_id", "template_id", "AND template_id IS NOT NULL"),
)


def _abort_if_duplicates() -> None:
    """Inspect the current data; raise if a new index would be violated."""
    bind = op.get_bind()
    conflicts: list[str] = []
    for label, expr, extra in _DUPLICATE_GROUPS:
        rows = bind.execute(
            sa.text(
                f"""
                SELECT org_id, {expr} AS key, COUNT(*) AS n,
                       array_agg(id ORDER BY created_at) AS ids
                FROM mcp_connector_installs
                WHERE install_state = 'active' {extra}
                GROUP BY org_id, {expr}
                HAVING COUNT(*) > 1
                """
            )
        ).all()
        for row in rows:
            conflicts.append(
                f"{label}: org={row.org_id} {label}={row.key!r} "
                f"count={row.n} ids={list(row.ids)}"
            )
    if conflicts:
        joined = "\n  ".join(conflicts)
        raise RuntimeError(
            "Cannot apply MCP install uniqueness migration — existing data "
            "violates the new R1/R2/R3 rule. Uninstall one row from each "
            "duplicate group and re-run alembic upgrade.\n  " + joined
        )


def upgrade() -> None:
    """Upgrade schema."""
    _abort_if_duplicates()

    # Drop the 6 per-scope partial unique indexes.
    for ix in (
        "uq_mcp_connector_install_name_org",
        "uq_mcp_connector_install_name_ws",
        "uq_mcp_connector_install_url_org",
        "uq_mcp_connector_install_url_ws",
        "uq_mcp_connector_install_per_template_org",
        "uq_mcp_connector_install_per_template_ws",
    ):
        op.drop_index(ix, table_name="mcp_connector_installs")

    # Create the 3 new org-wide partial unique indexes.
    op.create_index(
        "uq_mcp_connector_install_name_per_org",
        "mcp_connector_installs",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text("install_state = 'active'"),
    )
    op.create_index(
        "uq_mcp_connector_install_url_per_org",
        "mcp_connector_installs",
        ["org_id", "server_url_hash"],
        unique=True,
        postgresql_where=sa.text("install_state = 'active'"),
    )
    op.create_index(
        "uq_mcp_connector_install_template_per_org",
        "mcp_connector_installs",
        ["org_id", "template_id"],
        unique=True,
        postgresql_where=sa.text(
            "install_state = 'active' AND template_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Downgrade schema — restore the 6 per-scope partial unique indexes."""
    for ix in (
        "uq_mcp_connector_install_name_per_org",
        "uq_mcp_connector_install_url_per_org",
        "uq_mcp_connector_install_template_per_org",
    ):
        op.drop_index(ix, table_name="mcp_connector_installs")

    op.create_index(
        "uq_mcp_connector_install_url_org",
        "mcp_connector_installs",
        ["org_id", "server_url_hash"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NULL AND install_state = 'active'"
        ),
    )
    op.create_index(
        "uq_mcp_connector_install_url_ws",
        "mcp_connector_installs",
        ["org_id", "workspace_id", "server_url_hash"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NOT NULL AND install_state = 'active'"
        ),
    )
    op.create_index(
        "uq_mcp_connector_install_name_org",
        "mcp_connector_installs",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NULL AND install_state = 'active'"
        ),
    )
    op.create_index(
        "uq_mcp_connector_install_name_ws",
        "mcp_connector_installs",
        ["org_id", "workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NOT NULL AND install_state = 'active'"
        ),
    )
    op.create_index(
        "uq_mcp_connector_install_per_template_org",
        "mcp_connector_installs",
        ["org_id", "template_id"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )
    op.create_index(
        "uq_mcp_connector_install_per_template_ws",
        "mcp_connector_installs",
        ["org_id", "workspace_id", "template_id"],
        unique=True,
        postgresql_where=sa.text(
            "workspace_id IS NOT NULL AND template_id IS NOT NULL "
            "AND install_state = 'active'"
        ),
    )
