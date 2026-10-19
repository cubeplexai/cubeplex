"""Strict discovery, reviewed import, and activation API contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryResponse, ModelField


class DiscoveryRequest(CallingConfiguration):
    selected_model_ids: list[str] = Field(default_factory=list, max_length=100)


class SavedDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_model_ids: list[str] = Field(default_factory=list, max_length=100)


class ImportModelOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    purpose: Literal["chat"] | None = None
    context_window: int | None = Field(default=None, gt=0, strict=True)
    max_tokens: int | None = Field(default=None, gt=0, strict=True)
    input_modalities: list[Literal["text", "image"]] | None = Field(default=None, min_length=1)
    reasoning: bool | None = None
    capability: dict[str, Any] | None = None
    cost_input: float | None = Field(default=None, ge=0)
    cost_output: float | None = Field(default=None, ge=0)
    cost_cache_read: float | None = Field(default=None, ge=0)
    cost_cache_write: float | None = Field(default=None, ge=0)


class ImportModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=128)
    overrides: ImportModelOverrides = Field(default_factory=ImportModelOverrides)
    confirm_fields: list[ModelField] = Field(default_factory=list)
    resolutions: dict[ModelField, str] = Field(default_factory=dict)


class ImportModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovery_id: str = Field(min_length=1, max_length=64)
    models: list[ImportModelSelection] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_models(self) -> ImportModelsRequest:
        ids = [model.model_id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("selected model IDs must be unique")
        return self


class ImportModelOutcome(BaseModel):
    model_id: str
    model_db_id: str
    status: Literal["created", "existing"]


class ImportModelsResponse(BaseModel):
    results: list[ImportModelOutcome]


class EnableTestedModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_db_ids: list[str] = Field(min_length=1, max_length=100)


class EnableTestedModelOutcome(BaseModel):
    model_db_id: str
    status: Literal["enabled", "blocked"]
    reason: str | None = None


class EnableTestedModelsResponse(BaseModel):
    results: list[EnableTestedModelOutcome]


__all__ = [
    "DiscoveryRequest",
    "SavedDiscoveryRequest",
    "DiscoveryResponse",
    "ImportModelsRequest",
    "ImportModelsResponse",
    "EnableTestedModelsRequest",
    "EnableTestedModelsResponse",
]
