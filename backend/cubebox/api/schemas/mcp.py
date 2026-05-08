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


# ---------------- Catalog connector schemas ---------------- #


class MCPCatalogConnectorOut(BaseModel):
    """One catalog connector + per-(workspace, user) install status.

    Secret-bearing fields (``oauth_static_client_id``, the credential
    reference for the OAuth app secret) are intentionally omitted — the
    catalog endpoint is member-readable and must not leak them.
    """

    id: str
    slug: str
    name: str
    provider: str
    description: str
    server_url: str
    transport: str
    supported_auth_methods: list[str]
    default_credential_scope: str
    oauth_dcr_supported: bool | None
    oauth_default_scope: str | None
    static_form_fields: list[dict[str, Any]] | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    org_install_id: str | None
    workspace_visible: bool
    user_install_id: str | None


class MCPCatalogListOut(BaseModel):
    items: list[MCPCatalogConnectorOut]


class MCPCatalogInstallIn(BaseModel):
    """Admin install. ``scope=user`` installs into the admin's active workspace."""

    scope: Literal["org", "user"] = "org"
    auth_method: Literal["oauth", "static", "none"]
    auto_enable_workspaces: bool = True
    credential_plaintext: str | None = None
    credential_name: str | None = None


class MCPCatalogInstallWsIn(BaseModel):
    """Workspace user self-install — scope is forced to ``user`` server-side."""

    auth_method: Literal["oauth", "static", "none"]
    credential_plaintext: str | None = None
    credential_name: str | None = None


class MCPInstallSwitchAuthIn(BaseModel):
    """Re-key an existing install with a different auth method."""

    auth_method: Literal["oauth", "static", "none"]
    credential_plaintext: str | None = None


class MCPCatalogInstallOut(BaseModel):
    install_id: str
    requires_oauth: bool
    authed: bool


class MCPOrgInstallOverrideIn(BaseModel):
    """Toggle an org-wide install on/off for the calling workspace."""

    enabled: bool
