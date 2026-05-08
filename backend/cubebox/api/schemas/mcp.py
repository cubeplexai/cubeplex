"""MCP request/response schemas. Plaintext credentials only flow in."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CredentialRefOut(BaseModel):
    id: str
    name: str
    has_value: bool = True


class MCPServerOut(BaseModel):
    id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    credential_scope: str
    credential: CredentialRefOut | None
    owner_workspace_id: str | None
    headers: dict[str, str]
    tools_cache: list[dict[str, Any]] | None
    authed: bool
    last_error: str | None
    last_discovered_at: datetime | None
    timeout: float
    sse_read_timeout: float
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class MCPServerCreateAdmin(BaseModel):
    """Admin path: scope is org, user, or none."""

    name: str = Field(min_length=1, max_length=64)
    server_url: str = Field(min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["org", "user", "none"]
    credential_plaintext: str | None = None
    credential_name: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPServerCreateWS(BaseModel):
    """Workspace path: workspace-private server creation."""

    name: str = Field(min_length=1, max_length=64)
    server_url: str = Field(min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["workspace", "user", "none"]
    credential_plaintext: str | None = None
    credential_name: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPServerPatch(BaseModel):
    name: str | None = None
    server_url: str | None = None
    transport: Literal["streamable_http", "sse"] | None = None
    credential_plaintext: str | None = None
    headers: dict[str, str] | None = None
    timeout: float | None = None
    sse_read_timeout: float | None = None


class MCPTestConnectionRequest(BaseModel):
    server_url: str
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["org", "workspace", "user", "none"]
    credential_plaintext: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPTestConnectionResponse(BaseModel):
    success: bool
    tools: list[dict[str, Any]] | None = None
    error: str | None = None


class WorkspaceOverrideItem(BaseModel):
    workspace_id: str
    enabled: bool


class MCPOverrideUpdate(BaseModel):
    workspace_id: str
    enabled: bool


class MCPPromoteRequest(BaseModel):
    share_credential: bool = False


class MCPCredentialUpsert(BaseModel):
    plaintext: str = Field(min_length=1)
    name: str | None = None


class MCPCredentialStatus(BaseModel):
    has_value: bool


class MCPServerListWS(BaseModel):
    """Workspace GET response: owned servers and inherited org-wide servers."""

    owned: list[MCPServerOut]
    inherited: list[MCPServerOut]
