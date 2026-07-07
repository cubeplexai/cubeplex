"""MCP request/response schemas (four-layer surface only).

Plaintext credentials only flow in.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPOAuthStartIn(BaseModel):
    """Body for ``POST .../oauth/start``.

    ``frontend_origin`` lets the callback redirect the popup back to the
    browser's actual origin (e.g. ``http://192.168.1.215:3000``) instead
    of the static ``frontend_base_url`` config.  Carried through the
    HMAC-signed state token so it cannot be tampered with after issuance.
    """

    frontend_origin: str | None = None


class MCPOAuthStartOut(BaseModel):
    """Body of ``POST .../oauth/start``.

    The front-end OAuth controller stores ``state`` and filters
    BroadcastChannel messages by exact-match equality (spec §5.5).
    """

    authorize_url: str
    state: str
    expires_at: datetime


# ---------------- Four-layer (template / install / state / grant) schemas ---------------- #


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


CitationConfigJSON = dict[str, Any]  # opaque shape; agent runtime validates


class MCPToolEntry(BaseModel):
    """Single entry from ``MCPConnectorInstall.tools_cache``."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


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
    tools: list[MCPToolEntry]
    tool_citations: dict[str, CitationConfigJSON]
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


class MCPEffectiveConnectorListOut(BaseModel):
    """Envelope for list endpoints returning effective connectors."""

    items: list[MCPEffectiveConnectorOut]


class MCPAdminInstallEffectiveOut(BaseModel):
    """Org-row effective state for the admin page.

    Bypasses the workspace lens — see spec §4 admin row.
    """

    install_id: str
    usable: bool
    reason: Literal[
        "usable",
        "pending_oauth",
        "missing_org_grant",
        "grant_expired",
        "discovery_failed",
    ]


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

    # Org-policy static one-shot grant: when provided alongside a custom
    # static install with org-scope policy, the route creates the grant
    # immediately so the connector lands in a usable state on creation.
    credential_plaintext: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_policy_vs_auth(self) -> "AdminCreateInstallIn":
        if self.default_credential_policy == "none" and self.auth_method != "none":
            raise ValueError(
                "default_credential_policy='none' is only valid when auth_method='none'"
            )
        if self.auth_method == "none" and self.default_credential_policy != "none":
            raise ValueError("auth_method='none' requires default_credential_policy='none'")
        if self.template_id is None:
            # Custom install: name/server_url/transport are required.
            if not (self.name and self.server_url and self.transport):
                raise ValueError("name_server_url_transport_required_for_custom_installs")
        if self.credential_plaintext is not None:
            if self.auth_method != "static":
                raise ValueError("credential_plaintext_only_valid_with_static_auth")
            if self.default_credential_policy != "org":
                raise ValueError("credential_plaintext_only_valid_for_org_policy")
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

    auth_method: AuthMethodLiteral | None = None
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


class AdminInstallInvokeIn(BaseModel):
    """Body of ``POST /admin/mcp/installs/{id}/tools/{tool}/invoke``."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class WsInstallInvokeIn(BaseModel):
    """Body of ``POST /ws/{ws}/mcp/installs/{id}/tools/{tool}/invoke``."""

    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolInvokeOut(BaseModel):
    """Response of the Try It invoke routes."""

    ok: bool
    result: Any | None = None
    error: str | None = None
    duration_ms: int


class ToolCitationUpsertIn(BaseModel):
    """Body of ``PUT /admin/mcp/installs/{id}/tool-citations``.

    Sending ``config=None`` clears the entry for ``tool_name``; a non-null
    dict upserts the config as-is. The agent runtime validates the dict
    shape via :class:`CitationConfig` at tool-load time.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    config: dict[str, Any] | None = None


class PromoteInstallIn(BaseModel):
    """Body of ``POST /admin/mcp/installs/{id}/promote-to-org``."""

    model_config = ConfigDict(extra="forbid")

    distribution: AutoEnableIn = Field(default_factory=lambda: AutoEnableIn(mode="none"))


class TestConnectionIn(BaseModel):
    """Body of ``POST /admin/mcp/test-connection``.

    A connect probe used by the admin Custom-install form: tries to
    fetch ``tools/list`` without persisting anything. Static auth
    accepts an inline ``credential_plaintext`` (one-shot, not saved);
    other auth methods reject it (a misuse).
    """

    model_config = ConfigDict(extra="forbid")

    server_url: str
    transport: Literal["streamable_http", "sse"]
    auth_method: AuthMethodLiteral
    credential_plaintext: str | None = None
    headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_plaintext_only_with_static(self) -> "TestConnectionIn":
        if self.credential_plaintext is not None and self.auth_method != "static":
            raise ValueError("credential_plaintext_only_valid_with_static_auth")
        return self


class TestConnectionOut(BaseModel):
    """Response of ``POST /admin/mcp/test-connection``."""

    ok: bool
    tool_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


class AdminInstallRefreshIn(BaseModel):
    """Body of ``POST /admin/mcp/installs/{id}/refresh-discovery``.

    ``workspace_id`` is required when the install's default credential
    policy is workspace/user-scoped (the grant lookup needs the
    workspace lens). For org-policy installs it is left as None.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None


class WsInstallRefreshIn(BaseModel):
    """Body of ``POST /ws/{ws}/mcp/installs/{id}/refresh-discovery``.

    Workspace context comes from the path; body is empty.
    """

    model_config = ConfigDict(extra="forbid")


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


# ---------------------------------------------------------------------------
# Active-tools registry (GET /api/v1/ws/{ws}/mcp/active-tools)
# ---------------------------------------------------------------------------


class McpIconOut(BaseModel):
    """One icon entry (MCP spec rev 2025-11-25 ``Icon`` shape).

    ``src`` is either an HTTP/HTTPS URL or a ``data:`` URI. ``theme``
    is ``"light"`` / ``"dark"`` when the server supplies separate
    variants so the frontend can match the active UI theme.
    """

    src: str
    mime_type: str | None = None
    sizes: list[str] | None = None
    theme: str | None = None


class McpActiveToolOut(BaseModel):
    """One MCP tool surfaced to the workspace's chat UI.

    ``namespaced_name`` is the name the LLM sees and the SSE
    ``tool_call.name`` field carries — the frontend uses it as the
    lookup key. ``bare_name`` is what the MCP server originally called
    the tool (suitable as a display label next to the server icon).
    """

    namespaced_name: str
    bare_name: str
    install_id: str
    server_name: str
    server_icons: list[McpIconOut] = Field(default_factory=list)
    tool_icons: list[McpIconOut] = Field(default_factory=list)


class McpActiveToolListOut(BaseModel):
    items: list[McpActiveToolOut]
