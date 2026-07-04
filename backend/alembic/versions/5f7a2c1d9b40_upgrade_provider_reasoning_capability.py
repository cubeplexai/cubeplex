"""upgrade provider reasoning capability

Revision ID: 5f7a2c1d9b40
Revises: e7622d917897
Create Date: 2026-07-03 21:05:00.000000

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f7a2c1d9b40"
down_revision: str | Sequence[str] | None = "e7622d917897"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_KEYS = {"reasoning_off_payload", "reasoning_on_payload", "reasoning_level"}


def _effort_key(level: str) -> str:
    if level == "off":
        return "minimal"
    if level == "xhigh":
        return "max"
    return level


def _effort_value(level: str, value: Any) -> Any:
    if level == "off" and isinstance(value, int) and value <= 0:
        return 1024
    return value


def _convert_legacy_capability(value: Any) -> Any:
    if not isinstance(value, dict) or not any(key in value for key in _LEGACY_KEYS):
        return value

    converted = {key: val for key, val in value.items() if key not in _LEGACY_KEYS}
    reasoning = dict(converted.get("reasoning") or {})
    mode_payloads = dict(reasoning.get("mode_payloads") or {})

    off_payload = value.get("reasoning_off_payload")
    if isinstance(off_payload, dict):
        mode_payloads["off"] = off_payload
    on_payload = value.get("reasoning_on_payload")
    if isinstance(on_payload, dict):
        mode_payloads["on"] = on_payload
    if mode_payloads:
        reasoning["mode_payloads"] = mode_payloads

    legacy_level = value.get("reasoning_level")
    if isinstance(legacy_level, dict):
        path = legacy_level.get("path")
        if isinstance(path, str):
            reasoning["effort_path"] = path
        raw_values = legacy_level.get("level_budgets")
        if not isinstance(raw_values, dict):
            raw_values = legacy_level.get("level_to_effort")
        if isinstance(raw_values, dict):
            reasoning["effort_values"] = {
                _effort_key(str(level)): _effort_value(str(level), effort)
                for level, effort in raw_values.items()
            }
        reasoning["apply_effort_when_off"] = False

    if reasoning:
        converted["reasoning"] = reasoning
    return converted


def _convert_overrides(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        model_id: _convert_legacy_capability(capability) for model_id, capability in value.items()
    }


def upgrade() -> None:
    """Convert cached provider capability JSON from legacy reasoning keys."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, capability, model_capability_overrides FROM providers")
    ).fetchall()
    update = sa.text(
        "UPDATE providers SET capability = :capability, "
        "model_capability_overrides = :overrides WHERE id = :id"
    )

    for row in rows:
        capability = _convert_legacy_capability(row.capability)
        overrides = _convert_overrides(row.model_capability_overrides)
        if capability == row.capability and overrides == row.model_capability_overrides:
            continue
        bind.execute(
            update,
            {
                "id": row.id,
                "capability": json.dumps(capability),
                "overrides": json.dumps(overrides),
            },
        )


def downgrade() -> None:
    """No-op: the old capability shape dropped data under current cubepi."""
    pass
