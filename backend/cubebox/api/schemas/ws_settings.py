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


class MCPServerItem(BaseModel):
    server_id: str
    name: str
    server_url: str
    transport: str
    enabled: bool
    scope: str  # "org" | "workspace"
    credential_mode: str = "org"  # "org" | "workspace" | "user"
    credential_source: str | None = None  # "org" | "workspace" | "user" | "needs_setup" | None
    credential_shared_by: str | None = None  # display name when mode=workspace and cred exists


class WorkspaceMCPOut(BaseModel):
    org_servers: list[MCPServerItem]
    workspace_servers: list[MCPServerItem]


class MCPBindingPatch(BaseModel):
    enabled: bool | None = None
    credential_mode: str | None = None
