"""API schemas for model preset admin + workspace endpoints.

The admin body is structurally identical to the on-disk
OrgSettings.model_presets value, so we re-export the storage schema under
an API-namespaced name.
"""

from typing import Literal

from pydantic import BaseModel

from cubeplex.llm.snapshot_schema import ModelPresetsConfig as AdminModelPresetsBody


class WorkspacePresetSummary(BaseModel):
    key: str
    kind: Literal["tier", "custom"]
    primary: str
    description: str
    is_default: bool
    # Detail fields for composer picker (tooltip / brand heuristics).
    # Additive; older clients ignore. Null when primary cannot be resolved.
    provider_slug: str | None = None
    model_id: str | None = None
    model_display_name: str | None = None
    context_window: int | None = None
    reasoning: bool | None = None
    input_modalities: list[str] | None = None


class WorkspacePresetsResponse(BaseModel):
    presets: list[WorkspacePresetSummary]


__all__ = ["AdminModelPresetsBody", "WorkspacePresetSummary", "WorkspacePresetsResponse"]
