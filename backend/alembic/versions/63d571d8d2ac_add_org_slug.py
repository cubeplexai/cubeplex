"""add_org_slug

Revision ID: 63d571d8d2ac
Revises: 1a8d521ac153
Create Date: 2026-04-27 11:02:10.431824

"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '63d571d8d2ac'
down_revision: Union[str, Sequence[str], None] = '1a8d521ac153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add nullable slug column
    op.add_column(
        "organizations",
        sa.Column("slug", sa.String(length=32), nullable=True),
    )

    # 2. Backfill slugs for existing rows
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM organizations")).fetchall()
    used: set[str] = set()
    for row in rows:
        raw_base = _slugify(row.name)
        base = raw_base[:29]  # reserve room for -NN suffix
        candidate = base
        n = 2
        while candidate in used:
            candidate = f"{base}-{n}"
            n += 1
        used.add(candidate)
        bind.execute(
            sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row.id},
        )

    # 3. Enforce NOT NULL + UNIQUE
    op.alter_column("organizations", "slug", existing_type=sa.String(length=32), nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_slug", "organizations", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_constraint("uq_organizations_slug", "organizations", type_="unique")
    op.drop_column("organizations", "slug")


# Local copy of the slugify helper so the migration is hermetic.
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DEDUP = re.compile(r"-{2,}")


def _slugify(name: str) -> str:
    lowered = name.strip().lower()
    raw = _SLUG_RE.sub("-", lowered)
    deduped = _SLUG_DEDUP.sub("-", raw).strip("-")
    if not deduped:
        return "org"
    return deduped[:31].rstrip("-")
