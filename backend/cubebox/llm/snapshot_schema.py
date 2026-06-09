"""Pydantic schema for OrgSettings.model_presets row value.

Validates the JSON shape that admin endpoints write and that the snapshot
loader reads. Chain-entry format (provider/model-id) is not validated
here; the resolver and loader enforce ref well-formedness and
ref-exists-in-providers at request time.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

ALLOWED_TASKS: frozenset[str] = frozenset({"title", "compaction", "summarize"})

_LABEL_PATTERN = r"^[A-Za-z0-9_-]+$"


class LLMPresetSchema(BaseModel):
    label: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    chain: list[str] = Field(min_length=1)
    is_default: bool = False


class ModelPresetsValue(BaseModel):
    presets: list[LLMPresetSchema] = Field(min_length=1)
    task_presets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        labels = [p.label for p in self.presets]
        if len(set(labels)) != len(labels):
            raise ValueError("preset label must be unique")
        default_count = sum(1 for p in self.presets if p.is_default)
        if default_count != 1:
            raise ValueError(f"exactly one preset must be is_default=true (found {default_count})")
        for task_key in self.task_presets:
            if task_key not in ALLOWED_TASKS:
                raise ValueError(f"task_presets key {task_key!r} not in {sorted(ALLOWED_TASKS)}")
        for task_key, label in self.task_presets.items():
            if label not in labels:
                raise ValueError(f"task_presets[{task_key!r}]={label!r} not in preset labels")
        return self
