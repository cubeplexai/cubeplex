"""Pydantic response schemas for skill marketplace endpoints."""

from typing import Literal

from pydantic import BaseModel


class SkillFiles(BaseModel):
    rel_path: str
    size: int
    mime: str | None = None
    content_hash: str  # MD5 hex digest for change detection


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
    # Per-workspace effective binding: "auto" | "enabled" | "disabled" | null (not installed)
    workspace_binding_state: Literal["auto", "enabled", "disabled"] | None = None
    imported_from_registry_id: str | None = None
    imported_from_registry_name: str | None = None


class SkillVersionDetail(BaseModel):
    id: str
    version: str
    description: str
    keywords: list[str]
    storage_prefix: str
    entry_file: str
    uploaded_by_user_id: str | None
    created_at: str  # ISO-8601 with UTC offset


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
    auto_bind: bool | None = None  # None when not installed


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


class PatchInstallRequest(BaseModel):
    auto_bind: bool


class WorkspaceBindingsRequest(BaseModel):
    skill_ids: list[str]


class PublishFromArtifactRequest(BaseModel):
    artifact_id: str
