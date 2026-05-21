"""add provider slug

Revision ID: 538af47f81eb
Revises: eef196f4c8f9
Create Date: 2026-05-21 13:00:12.938290

"""

import json
import re
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "538af47f81eb"
down_revision: Union[str, Sequence[str], None] = "eef196f4c8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    collapsed = _NON_SLUG.sub("-", name.lower()).strip("-")
    return collapsed or "provider"


def _assign(base: str, taken: set[str]) -> str:
    base = base or "provider"
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        candidate = base[: 64 - len(suffix)] + suffix  # always fits the 64-char column
        if candidate not in taken:
            return candidate
        n += 1


def _rewrite_ref(ref: str, name_to_slug: dict[str, str]) -> str:
    parts = ref.split("/", 1)
    if len(parts) != 2:
        return ref
    provider_name, model_id = parts
    slug = name_to_slug.get(provider_name)
    return f"{slug}/{model_id}" if slug else ref


def upgrade() -> None:
    bind = op.get_bind()

    # 1. add nullable column
    op.add_column("providers", sa.Column("slug", sa.String(length=64), nullable=True))

    # 2. backfill slug. System providers (org_id IS NULL) first so org providers
    #    can also dedup against system slugs — an org provider must NOT reuse a
    #    slug that's visible system-wide, or resolution within that org would be
    #    ambiguous. Two orgs may still share a slug (separate resolution contexts).
    rows = bind.execute(
        sa.text(
            "SELECT id, org_id, name FROM providers "
            "ORDER BY (org_id IS NOT NULL), created_at, id"  # NULL (system) first
        )
    ).fetchall()
    upd = sa.text("UPDATE providers SET slug = :slug WHERE id = :id")
    system_slugs: set[str] = set()
    org_used: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        base = _slugify(row.name)
        if row.org_id is None:
            slug = _assign(base, system_slugs)
            system_slugs.add(slug)
        else:
            slug = _assign(base, org_used[row.org_id] | system_slugs)
            org_used[row.org_id].add(slug)
        bind.execute(upd, {"slug": slug, "id": row.id})

    # 3. NOT NULL + index + partial unique indexes (org bucket + system bucket).
    #    Two partial indexes (not one composite constraint) so the org_id=NULL
    #    system bucket is also uniquely constrained. Mirrors models/credential.py.
    op.alter_column("providers", "slug", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_providers_slug", "providers", ["slug"])
    op.create_index(
        "uq_provider_org_slug",
        "providers",
        ["org_id", "slug"],
        unique=True,
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_index(
        "uq_provider_system_slug",
        "providers",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("org_id IS NULL"),
    )

    # 4. rewrite OrgSettings refs name->slug, per org
    #    refs can point at system providers (org_id NULL) too, so the name->slug
    #    map for an org includes that org's providers AND the system bucket.
    prov_rows = bind.execute(sa.text("SELECT org_id, name, slug FROM providers")).fetchall()
    system_map: dict[str, str] = {}
    org_maps: dict[str, dict[str, str]] = defaultdict(dict)
    for r in prov_rows:
        if r.org_id is None:
            system_map[r.name] = r.slug
        else:
            org_maps[r.org_id][r.name] = r.slug

    settings_rows = bind.execute(
        sa.text(
            "SELECT org_id, key, value FROM org_settings "
            "WHERE key IN ('default_model', 'fallback_models', 'task_models')"
        )
    ).fetchall()
    set_value = sa.text(
        "UPDATE org_settings SET value = :value WHERE org_id = :org_id AND key = :key"
    )
    for s in settings_rows:
        name_to_slug = {**system_map, **org_maps.get(s.org_id, {})}
        value = s.value if isinstance(s.value, dict) else json.loads(s.value)
        if s.key == "default_model" and value.get("model_ref"):
            value = {**value, "model_ref": _rewrite_ref(str(value["model_ref"]), name_to_slug)}
        elif s.key == "fallback_models" and value.get("models"):
            value = {
                **value,
                "models": [_rewrite_ref(str(m), name_to_slug) for m in value["models"]],
            }
        elif s.key == "task_models":
            value = {
                **value,
                **{
                    task: _rewrite_ref(str(ref), name_to_slug)
                    for task, ref in value.items()
                    if isinstance(ref, str)
                },
            }
        else:
            continue
        bind.execute(set_value, {"value": json.dumps(value), "org_id": s.org_id, "key": s.key})


def downgrade() -> None:
    op.drop_index("uq_provider_system_slug", table_name="providers")
    op.drop_index("uq_provider_org_slug", table_name="providers")
    op.drop_index("ix_providers_slug", table_name="providers")
    op.drop_column("providers", "slug")
