"""MCP request/response schemas. Plaintext credentials only flow in."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """Admin install. Always org-wide — user-scope goes via the workspace endpoint."""

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
    credential_name: str | None = None


class MCPCatalogInstallOut(BaseModel):
    install_id: str
    requires_oauth: bool
    authed: bool


class MCPOrgInstallOverrideIn(BaseModel):
    """Toggle an org-wide install on/off for the calling workspace."""

    enabled: bool


class MCPOAuthStartIn(BaseModel):
    """Empty body for ``POST .../oauth/start`` — present for OpenAPI clarity."""


class MCPOAuthStartOut(BaseModel):
    """Response of ``POST .../oauth/start``."""

    authorize_url: str
    state: str


# ---------------- Four-layer (template / install / state / grant) schemas ---------------- #
#
# Coexist with the legacy MCPServer / MCPCatalog schemas above; Task 9 of the
# four-layer plan removes the legacy ones once the frontend has migrated.


CredentialPolicyLiteral = Literal["org", "workspace", "user", "none"]
AuthMethodLiteral = Literal["oauth", "static", "none"]


class MCPConnectorTemplateOut(BaseModel):
    """Template catalog row (public/admin-visible)."""

    template_id: str
    slug: str
    name: str
    provider: str
    description: str
    server_url: str
    transport: str
    supported_auth_methods: list[str]
    default_credential_policy: CredentialPolicyLiteral
    static_form_schema: list[dict[str, Any]] | None
    status: str
    install_summary: dict[str, Any] | None = None


class MCPConnectorInstallOut(BaseModel):
    """One ``MCPConnectorInstall`` row."""

    install_id: str
    template_id: str | None
    install_scope: Literal["org", "workspace"]
    workspace_id: str | None
    name: str
    server_url: str
    transport: str
    auth_method: AuthMethodLiteral
    default_credential_policy: CredentialPolicyLiteral
    auth_status: str
    discovery_status: str
    install_state: str
    tool_count: int
    last_error: str | None
    auto_enroll_new_workspaces: bool


class MCPWorkspaceConnectorStateOut(BaseModel):
    """One ``MCPWorkspaceConnectorState`` row."""

    workspace_id: str
    install_id: str
    enabled: bool
    credential_policy: CredentialPolicyLiteral
    enablement_source: str


class MCPCredentialGrantStatusOut(BaseModel):
    """Status of one ``MCPCredentialGrant`` (or absence thereof)."""

    install_id: str
    grant_scope: Literal["org", "workspace", "user"]
    workspace_id: str | None
    user_id: str | None
    grant_status: str
    has_value: bool
    expires_at: datetime | None


class MCPEffectiveConnectorOut(BaseModel):
    """One effective connector row as returned by GET /ws/{ws}/mcp/connectors."""

    template: MCPConnectorTemplateOut | None
    install: MCPConnectorInstallOut
    workspace_state: MCPWorkspaceConnectorStateOut | None
    credential_policy: CredentialPolicyLiteral
    required_grant_scope: str | None
    credential_availability: Literal["available", "missing", "not_required"]
    credential_source: Literal["org", "workspace", "user"] | None
    usable: bool
    reason: str


class MCPConnectorTemplateListOut(BaseModel):
    """Envelope for list endpoints returning connector templates."""

    items: list[MCPConnectorTemplateOut]


class MCPConnectorInstallListOut(BaseModel):
    """Envelope for list endpoints returning connector installs."""

    items: list[MCPConnectorInstallOut]


class MCPEffectiveConnectorListOut(BaseModel):
    """Envelope for list endpoints returning effective connectors."""

    items: list[MCPEffectiveConnectorOut]


class AutoEnableIn(BaseModel):
    """Distribution payload for org-scope installs."""

    mode: Literal["all", "selected", "none"]
    workspace_ids: list[str] | None = None


class AdminCreateInstallIn(BaseModel):
    """Body of POST /api/v1/admin/mcp/installs.

    Cross-field validation: ``credential_policy="none"`` is allowed only when
    ``auth_method="none"`` — otherwise the install would be a credentialed
    connector with no grant slot.
    """

    model_config = ConfigDict(extra="forbid")

    template_id: str | None = None
    install_scope: Literal["org"] = "org"
    auth_method: AuthMethodLiteral
    default_credential_policy: CredentialPolicyLiteral
    auto_enable: AutoEnableIn = Field(default_factory=lambda: AutoEnableIn(mode="none"))

    # Custom-install fields (used when template_id is None or to override the
    # template). Optional in both cases.
    name: str | None = Field(default=None, min_length=1, max_length=64)
    server_url: str | None = Field(default=None, min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse"] | None = None
    headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_policy_vs_auth(self) -> "AdminCreateInstallIn":
        if self.default_credential_policy == "none" and self.auth_method != "none":
            raise ValueError(
                "default_credential_policy='none' is only valid when auth_method='none'"
            )
        if self.auth_method == "none" and self.default_credential_policy != "none":
            raise ValueError("auth_method='none' requires default_credential_policy='none'")
        return self


class WorkspaceCreateInstallIn(BaseModel):
    """Body of POST /api/v1/ws/{workspace_id}/mcp/installs.

    Mirrors :class:`AdminCreateInstallIn` but pins ``install_scope`` to
    ``"workspace"`` so the workspace install handler distinguishes its
    request shape from the admin shape at the schema layer. The
    ``credential_policy='none'`` cross-field validator is kept in sync.
    """

    model_config = ConfigDict(extra="forbid")

    template_id: str | None = None
    install_scope: Literal["workspace"] = "workspace"
    auth_method: AuthMethodLiteral
    default_credential_policy: CredentialPolicyLiteral

    # Custom-install fields (used when template_id is None or to override the
    # template). Optional in both cases.
    name: str | None = Field(default=None, min_length=1, max_length=64)
    server_url: str | None = Field(default=None, min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse"] | None = None
    headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_policy_vs_auth(self) -> "WorkspaceCreateInstallIn":
        if self.default_credential_policy == "none" and self.auth_method != "none":
            raise ValueError(
                "default_credential_policy='none' is only valid when auth_method='none'"
            )
        if self.auth_method == "none" and self.default_credential_policy != "none":
            raise ValueError("auth_method='none' requires default_credential_policy='none'")
        return self


class PatchInstallIn(BaseModel):
    """Body of PATCH /api/v1/admin/mcp/installs/{install_id}.

    Reject unknown keys via ``extra="forbid"``. The auth_method ↔ policy
    pairing cannot be validated here (body may omit one); the service layer
    re-validates with the loaded install row.
    """

    model_config = ConfigDict(extra="forbid")

    default_credential_policy: CredentialPolicyLiteral | None = None
    auto_enroll_new_workspaces: bool | None = None
    headers: dict[str, str] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64)
    server_url: str | None = Field(default=None, min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse"] | None = None


class PatchWorkspaceStateIn(BaseModel):
    """Body of PATCH /api/v1/ws/{ws}/mcp/connectors/{install_id}/state.

    ``extra="forbid"`` keeps the contract narrow. The auth_method ↔ policy
    pairing is re-validated server-side against the loaded install row.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    credential_policy: CredentialPolicyLiteral | None = None


class CreateGrantIn(BaseModel):
    """Body of POST /api/v1/.../grants/{org|workspace|me}.

    For static grants: ``credential_plaintext`` is required.
    For OAuth callback resolution: ``oauth_callback_state`` (the state token)
    is required.
    For OAuth start: body may be empty — that flow uses the dedicated
    ``/grants/<scope>/oauth/start`` route instead.
    """

    model_config = ConfigDict(extra="forbid")

    credential_plaintext: str | None = Field(default=None, min_length=1)
    oauth_callback_state: str | None = None
    name: str | None = None
