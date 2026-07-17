"""migrate orgsettings to model_presets

Revision ID: 3e8d2018cdb9
Revises: fef7470bc3a5
Create Date: 2026-06-09 21:12:09.835213

Translates OrgSettings rows with keys in
{'default_model', 'fallback_models', 'task_models'} into a single
'model_presets' row per (org_id) tuple. No table schema changes.

Note: ``ALLOWED_TASKS`` is inlined (not imported from
``cubeplex.llm.snapshot_schema``) so this migration stays frozen against
future schema drift. Legacy ``task_models`` keys outside this set (e.g.
the historical ``"chat"`` key) are dropped on purpose — they previously
fell through to the default model, and should continue doing so under
the new ``ModelPresetsValue`` schema, which would otherwise reject them
during Pydantic validation and 500 every request.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from cubeplex.models.public_id import PREFIX_ORG_SETTING, generate_public_id

revision: str = "3e8d2018cdb9"
down_revision: Union[str, Sequence[str], None] = "fef7470bc3a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_KEYS = ("default_model", "fallback_models", "task_models")
NEW_KEY = "model_presets"
ALLOWED_TASKS = frozenset({"title", "compaction", "summarize"})


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT org_id, key, value FROM org_settings WHERE key = ANY(:keys)"),
        {"keys": list(LEGACY_KEYS)},
    ).fetchall()

    by_org: dict[str | None, dict[str, dict]] = {}
    for r in rows:
        by_org.setdefault(r.org_id, {})[r.key] = r.value

    for org_id, legacy in by_org.items():
        default_ref = (legacy.get("default_model") or {}).get("model_ref")
        fallback_refs = (legacy.get("fallback_models") or {}).get("models") or []
        task_models = legacy.get("task_models") or {}

        if not default_ref:
            # Nothing to translate; seeder will fill later.
            continue

        presets = [
            {
                "label": "default",
                "chain": [default_ref] + list(fallback_refs),
                "is_default": True,
            }
        ]
        task_presets: dict[str, str] = {}
        for task_key, ref in task_models.items():
            if task_key not in ALLOWED_TASKS:
                # Drop legacy keys like 'chat' — they used to fall through
                # to default and should continue doing so under the new schema.
                continue
            if not ref or ref == default_ref:
                continue
            label = f"task-{task_key}"
            presets.append({"label": label, "chain": [ref], "is_default": False})
            task_presets[task_key] = label

        new_value = {"presets": presets, "task_presets": task_presets}
        new_id = generate_public_id(PREFIX_ORG_SETTING)
        # NOTE: A7.5 introduced surrogate `id` PK on org_settings.
        # Inserting a fresh row with a new id; existing model_presets row
        # for this org (if any) is left untouched — admin edits win.
        conn.execute(
            sa.text(
                """
                INSERT INTO org_settings (id, org_id, key, value, created_at, updated_at)
                VALUES (:id, :org_id, :key, :value, now(), now())
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": new_id,
                "org_id": org_id,
                "key": NEW_KEY,
                "value": json.dumps(new_value),
            },
        )

    conn.execute(
        sa.text("DELETE FROM org_settings WHERE key = ANY(:keys)"),
        {"keys": list(LEGACY_KEYS)},
    )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT org_id, value FROM org_settings WHERE key = :key"),
        {"key": NEW_KEY},
    ).fetchall()
    for r in rows:
        v = r.value
        default = next((p for p in v.get("presets", []) if p.get("is_default")), None)
        if default is None:
            continue
        new_id_default = generate_public_id(PREFIX_ORG_SETTING)
        conn.execute(
            sa.text(
                """
                INSERT INTO org_settings (id, org_id, key, value, created_at, updated_at)
                VALUES (:id, :org_id, 'default_model', :v, now(), now())
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": new_id_default,
                "org_id": r.org_id,
                "v": json.dumps({"model_ref": default["chain"][0]}),
            },
        )
        if len(default["chain"]) > 1:
            new_id_fb = generate_public_id(PREFIX_ORG_SETTING)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO org_settings (id, org_id, key, value, created_at, updated_at)
                    VALUES (:id, :org_id, 'fallback_models', :v, now(), now())
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": new_id_fb,
                    "org_id": r.org_id,
                    "v": json.dumps({"models": default["chain"][1:]}),
                },
            )
        task_models = {
            t: next(p for p in v["presets"] if p["label"] == label)["chain"][0]
            for t, label in v.get("task_presets", {}).items()
        }
        if task_models:
            new_id_tm = generate_public_id(PREFIX_ORG_SETTING)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO org_settings (id, org_id, key, value, created_at, updated_at)
                    VALUES (:id, :org_id, 'task_models', :v, now(), now())
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": new_id_tm,
                    "org_id": r.org_id,
                    "v": json.dumps(task_models),
                },
            )

    conn.execute(sa.text("DELETE FROM org_settings WHERE key = :key"), {"key": NEW_KEY})
