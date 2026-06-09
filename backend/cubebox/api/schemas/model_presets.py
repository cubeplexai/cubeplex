"""API schemas for model preset admin + workspace endpoints.

The admin body is structurally identical to the on-disk
OrgSettings.model_presets value, so we re-export the existing schema
under an API-namespaced name.
"""

from pydantic import BaseModel

from cubebox.llm.snapshot_schema import LLMPresetSchema as AdminPresetEntry
from cubebox.llm.snapshot_schema import ModelPresetsValue as AdminModelPresetsBody

__all__ = [
    "AdminModelPresetsBody",
    "AdminPresetEntry",
    "WorkspacePresetSummary",
    "WorkspacePresetsResponse",
]


class WorkspacePresetSummary(BaseModel):
    """Per-preset summary exposed to workspace users (no chain refs)."""

    label: str
    is_default: bool


class WorkspacePresetsResponse(BaseModel):
    presets: list[WorkspacePresetSummary]
