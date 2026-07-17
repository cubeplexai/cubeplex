"""OrgSettings — per-org key-value settings for LLM defaults.

The table now carries a surrogate ``id`` PK and a *nullable* ``org_id``.
A row with ``org_id IS NULL`` is the system-level fallback for a given
``key``; org-owned rows override it. Uniqueness is enforced by two
partial indexes:

* ``uq_org_settings_org_key`` — unique on ``(org_id, key)`` where
  ``org_id IS NOT NULL``.
* ``uq_org_settings_system_key`` — unique on ``key`` where
  ``org_id IS NULL``.

This mirrors the system-row pattern used by ``credentials``
(see ``alembic/versions/d44dff875e38_*``).
"""

from typing import Any

from sqlalchemy import Column, Index, text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from cubeplex.models.mixins import TimestampMixin
from cubeplex.models.public_id import PREFIX_ORG_SETTING, generate_public_id

# Per-org model presets + per-task preset routing.
# Schema lives in cubeplex.llm.snapshot_schema.ModelPresetsValue.
MODEL_PRESETS_KEY = "model_presets"


class OrgSettings(SQLModel, TimestampMixin, table=True):
    """Per-org (or system-level) key-value settings store.

    ``org_id IS NULL`` indicates a system-level fallback row; otherwise
    the row is owned by the referenced organization. Partial unique
    indexes (see module docstring) enforce one row per ``(org_id, key)``
    and one system row per ``key``.
    """

    __tablename__ = "org_settings"
    __table_args__ = (
        Index(
            "uq_org_settings_org_key",
            "org_id",
            "key",
            unique=True,
            postgresql_where="org_id IS NOT NULL",
            sqlite_where=text("org_id IS NOT NULL"),
        ),
        Index(
            "uq_org_settings_system_key",
            "key",
            unique=True,
            postgresql_where="org_id IS NULL",
            sqlite_where=text("org_id IS NULL"),
        ),
    )

    id: str = Field(
        primary_key=True,
        max_length=20,
        default_factory=lambda: generate_public_id(PREFIX_ORG_SETTING),
    )
    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True
    )
    key: str = Field(max_length=64)
    value: dict[str, Any] = Field(sa_column=Column(JSON))
