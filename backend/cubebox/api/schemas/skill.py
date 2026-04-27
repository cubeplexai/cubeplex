"""Pydantic response schemas for skill marketplace endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SkillFiles(BaseModel):
    rel_path: str
    size: int
    mime: str | None = None


class SkillSummary(BaseModel):
    """Row in the marketplace list view."""

    id: str
    name: str
    source: Literal["preinstalled", "uploaded"]
    description: str
    current_version: str
    keywords: list[str]
    install_state: Literal["uninstalled", "installed", "update_available"]
    installed_version: str | None = None
    workspace_bindings_count: int = 0


class SkillVersionDetail(BaseModel):
    id: str
    version: str
    description: str
    keywords: list[str]
    storage_prefix: str
    entry_file: str
    uploaded_by_user_id: str | None
    created_at: datetime


class SkillDetail(BaseModel):
    id: str
    name: str
    source: Literal["preinstalled", "uploaded"]
    description: str
    current_version: str
    keywords: list[str]
    versions: list[SkillVersionDetail]
    install_state: Literal["uninstalled", "installed", "update_available"]
    installed_version: str | None = None


class SkillContentResponse(BaseModel):
    """Used by preview endpoints; returns SKILL.md content + sibling files list."""

    skill_id: str
    skill_version_id: str
    name: str
    version: str
    content: str
    files: list[SkillFiles]


class InstallRequest(BaseModel):
    version: str


class WorkspaceBindingsRequest(BaseModel):
    skill_ids: list[str]


class PublishFromArtifactRequest(BaseModel):
    artifact_id: str
