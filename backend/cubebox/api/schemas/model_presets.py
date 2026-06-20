"""API schemas for model preset admin + workspace endpoints.

The admin body is structurally identical to the on-disk
OrgSettings.model_presets value, so we re-export the storage schema under
an API-namespaced name.
"""

from typing import Literal

from pydantic import BaseModel

from cubebox.llm.snapshot_schema import ModelPresetsConfig as AdminModelPresetsBody


class WorkspacePresetSummary(BaseModel):
    key: str
    kind: Literal["tier", "custom"]
    primary: str
    description: str
    is_default: bool


class WorkspacePresetsResponse(BaseModel):
    presets: list[WorkspacePresetSummary]


__all__ = ["AdminModelPresetsBody", "WorkspacePresetSummary", "WorkspacePresetsResponse"]
