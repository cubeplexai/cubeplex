"""Pydantic schema for the OrgSettings.model_presets row value.

Structured authoring shape: at least one of the four built-in tiers + admin
custom presets + a default + task routing. Omitted tiers are treated as
disabled (see `_load_presets` in snapshot.py, which defaults a missing tier to
`TierSetting()`). Tier descriptions are NOT stored here (fixed i18n copy in the
frontend). Ref well-formedness / ref-exists-in-providers is enforced at
write/resolve time, not here.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

_LABEL_PATTERN = r"^[A-Za-z0-9_-]+$"


class ModelTier(StrEnum):
    lite = "lite"
    flash = "flash"
    pro = "pro"
    max = "max"


class TaskKey(StrEnum):
    title = "title"  # type: ignore[assignment]  # name shadows str.title method
    summarize = "summarize"
    compaction = "compaction"


_TIER_NAMES: frozenset[str] = frozenset(t.value for t in ModelTier)


class TierSetting(BaseModel):
    enabled: bool = False
    primary: str | None = None
    fallbacks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enabled_needs_primary(self) -> Self:
        if self.enabled and not self.primary:
            raise ValueError("an enabled tier must have a primary model")
        return self


class CustomPreset(BaseModel):
    label: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    primary: str = Field(min_length=1)
    fallbacks: list[str] = Field(default_factory=list)
    description: str = ""


class ModelPresetsConfig(BaseModel):
    tiers: dict[ModelTier, TierSetting]
    custom_presets: list[CustomPreset] = Field(default_factory=list)
    default_preset: str
    task_routing: dict[TaskKey, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if not self.tiers:
            raise ValueError("tiers must contain at least one tier")
        available: set[str] = {t.value for t, s in self.tiers.items() if s.enabled and s.primary}
        labels = [c.label for c in self.custom_presets]
        if len(set(labels)) != len(labels):
            raise ValueError("custom preset labels must be unique")
        for label in labels:
            if label in _TIER_NAMES:
                raise ValueError(f"custom label {label!r} collides with a tier name")
        available |= set(labels)
        if self.default_preset not in available:
            raise ValueError(f"default_preset {self.default_preset!r} is not an available preset")
        for task, key in self.task_routing.items():
            if key not in available:
                raise ValueError(f"task_routing[{task.value!r}]={key!r} is not an available preset")
        return self
