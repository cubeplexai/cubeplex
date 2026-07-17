"""Pydantic schemas for workspace settings endpoints."""

from pydantic import BaseModel, Field


class AgentConfigOut(BaseModel):
    system_prompt: str


class AgentConfigPatch(BaseModel):
    system_prompt: str = Field(max_length=8000)


class SkillInstallOut(BaseModel):
    install_id: str
    skill_id: str
    name: str
    description: str
    installed_version: str
    enabled: bool
    scope: str  # "org" | "workspace"


class WorkspaceSkillsOut(BaseModel):
    org_skills: list[SkillInstallOut]
    workspace_skills: list[SkillInstallOut]


class SkillBindingPatch(BaseModel):
    enabled: bool


class SkillInstallCreate(BaseModel):
    skill_id: str
    version: str
