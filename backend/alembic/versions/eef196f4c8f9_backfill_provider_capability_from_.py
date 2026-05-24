"""backfill provider capability from cubepi catalog

Revision ID: eef196f4c8f9
Revises: affde7178172
Create Date: 2026-05-20 07:45:54.608116

Data migration (no schema change): backfill the cached capability snapshot onto
existing provider rows whose ``capability`` is still empty. The mapping key is
the provider ``name`` matched exactly against a cubepi preset slug; rows with no
matching preset are left untouched.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'eef196f4c8f9'
down_revision: Union[str, Sequence[str], None] = 'affde7178172'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill preset_slug + capability + overrides on rows with empty capability.

    The preset catalog was later removed from cubepi (provider presets dropped
    upstream), so the import is local and the backfill is a no-op when the
    catalog is unavailable — every existing DB already ran this revision, and a
    fresh DB on the catalog-less cubepi has no presets to backfill from.
    """
    try:
        from cubepi.providers.catalog import list_provider_presets
    except ImportError:
        return
    presets = {p.slug: p for p in list_provider_presets()}

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, name FROM providers "
            "WHERE capability IS NULL OR capability::text = '{}'"
        )
    ).fetchall()

    update = sa.text(
        "UPDATE providers SET preset_slug = :slug, capability = :capability, "
        "model_capability_overrides = :overrides WHERE id = :id"
    )

    for row in rows:
        preset = presets.get(row.name)
        if preset is None:
            continue
        capability = preset.capability.model_dump(mode="json")
        overrides = {
            mid: cap.model_dump(mode="json")
            for mid, cap in preset.model_capability_overrides.items()
        }
        bind.execute(
            update,
            {
                "id": row.id,
                "slug": preset.slug,
                "capability": json.dumps(capability),
                "overrides": json.dumps(overrides),
            },
        )


def downgrade() -> None:
    """No-op: a data backfill of cached snapshots is not cleanly reversible.

    We cannot distinguish backfilled values from admin-edited ones, so clearing
    them on downgrade would risk data loss. Leave the data in place.
    """
    pass
