"""OrgSettings — per-org key-value settings for LLM defaults."""

from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import TimestampMixin

# Per-task model routing. value shape:
#   {"chat": "<provider/model>", "title": "...", "summarize": "..."}
# All keys optional; a missing key falls back to default_model.
TASK_MODELS_KEY = "task_models"

# Replacement for the legacy default_model / fallback_models / task_models keys.
# Schema lives in cubebox.llm.snapshot_schema.ModelPresetsValue.
MODEL_PRESETS_KEY = "model_presets"


class OrgSettings(SQLModel, TimestampMixin, table=True):
    """Per-org key-value settings store; composite PK (org_id, key)."""

    __tablename__ = "org_settings"

    org_id: str = Field(primary_key=True, foreign_key="organizations.id", max_length=20)
    key: str = Field(primary_key=True, max_length=64)
    value: dict[str, Any] = Field(sa_column=Column(JSON))
