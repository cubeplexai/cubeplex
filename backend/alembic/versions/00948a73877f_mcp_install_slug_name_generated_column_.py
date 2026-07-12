"""mcp install slug_name column + slug-based uniqueness

Revision ID: 00948a73877f
Revises: 47105505d677
Create Date: 2026-05-18 19:03:21.232423

The previous migration enforced uniqueness on raw ``name`` — but the
cubepi runtime's tool slug runs ``re.sub('[^a-zA-Z0-9]+', '_', name).strip('_')``
before namespacing tools. Display names that differ only by stripped
characters (``Web Tools`` vs ``Web-Tools``) collapse to the same slug
``Web_Tools`` and would still collide at the LLM layer.

Fix: index the canonical slug rather than the display string.

Schema: ``slug_name`` is a regular TEXT column kept in sync with
``name`` by a SQLAlchemy ``before_insert`` / ``before_update`` event
listener (see ``cubeplex.models.mcp``). A Postgres GENERATED column
would be cleaner but uses regex syntax SQLite (the unit-test driver
in some paths) doesn't accept; the ORM invariant is the portable form.

Migration steps:
1. Add ``slug_name`` as a TEXT column with a server default of ``'mcp'``
   so the NOT NULL column can be added without a separate backfill.
2. Backfill existing rows using the same Postgres expression so the
   value matches what Python would compute for the same ``name``.
3. Abort if any pair of active rows now share a slug (option B —
   manual operator cleanup, same pattern as the previous migration).
4. Drop the ``(org_id, name)`` partial unique index.
5. Create the ``(org_id, slug_name)`` partial unique index.

Downgrade reverses 3-5. The column drop on step 5 reverses step 1.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "00948a73877f"
down_revision: Union[str, Sequence[str], None] = "47105505d677"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# MUST mirror ``cubeplex.mcp._constants.slugify_for_namespace`` Python regex.
_SLUG_PG_EXPRESSION = (
    "COALESCE(NULLIF(TRIM(BOTH '_' FROM regexp_replace(name, '[^a-zA-Z0-9]+', '_', 'g')), ''), "
    "'mcp')"
)


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add the column with a server default so the NOT NULL clause is
    #    legal at add-time. Existing rows initialise to ``'mcp'``; we
    #    overwrite them in step 2.
    op.add_column(
        "mcp_connector_installs",
        sa.Column(
            "slug_name",
            sa.String(length=72),
            nullable=False,
            server_default=sa.text("'mcp'"),
        ),
    )

    # 2. Backfill all existing rows from ``name`` using the Postgres
    #    expression that mirrors ``slugify_for_namespace``.
    op.execute(
        sa.text(
            f"UPDATE mcp_connector_installs SET slug_name = {_SLUG_PG_EXPRESSION}"
        )
    )

    # 3. Abort if active rows now share a slug. Transactional DDL rolls
    #    the column add back so re-running after cleanup starts clean.
    bind = op.get_bind()
    dups = bind.execute(
        sa.text(
            """
            SELECT org_id, slug_name, COUNT(*) AS n,
                   array_agg(id ORDER BY created_at) AS ids,
                   array_agg(name ORDER BY created_at) AS names
            FROM mcp_connector_installs
            WHERE install_state = 'active'
            GROUP BY org_id, slug_name
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    if dups:
        lines = [
            f"org={r.org_id} slug={r.slug_name!r} count={r.n} "
            f"names={list(r.names)} ids={list(r.ids)}"
            for r in dups
        ]
        raise RuntimeError(
            "Cannot apply slug_name uniqueness — existing data violates the new rule. "
            "Uninstall one row from each group and re-run alembic upgrade.\n  "
            + "\n  ".join(lines)
        )

    # 4. Swap the (org_id, name) index for (org_id, slug_name).
    op.drop_index(
        "uq_mcp_connector_install_name_per_org",
        table_name="mcp_connector_installs",
    )
    op.create_index(
        "uq_mcp_connector_install_slug_per_org",
        "mcp_connector_installs",
        ["org_id", "slug_name"],
        unique=True,
        postgresql_where=sa.text("install_state = 'active'"),
    )


def downgrade() -> None:
    """Downgrade schema — drop slug index/column, restore name index."""
    op.drop_index(
        "uq_mcp_connector_install_slug_per_org",
        table_name="mcp_connector_installs",
    )
    op.create_index(
        "uq_mcp_connector_install_name_per_org",
        "mcp_connector_installs",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text("install_state = 'active'"),
    )
    op.drop_column("mcp_connector_installs", "slug_name")
