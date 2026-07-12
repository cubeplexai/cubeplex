"""MCP catalog and template schemas (template-centric model).

Catalog APIs return effective state combining:
  - MCPTemplateOut: the template metadata (always present)
  - MCPConnectorFactsOut: the active connector state, if any (None when no install)
  - Admin/workspace-specific availability / enablement fields
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .mcp import MCPToolEntry


class MCPTemplateOut(BaseModel):
    """One MCP template row (catalog row template metadata)."""

    template_id: str
    slug: str
    name: str
    provider: str
    description: str
    scope: Literal["global", "org", "workspace"]
    workspace_id: str | None
    server_url: str
    transport: str
    supported_auth_methods: list[str]
    default_credential_policy: str
    status: str


class MCPConnectorFactsOut(BaseModel):
    """Facts about one active MCP connector for catalog display.

    Contains discovery and tooling info for the installed connector.
    Omitted when no connector has been installed yet.
    """

    connector_id: str
    default_credential_policy: str
    discovery_status: str
    tool_count: int
    tools: list[MCPToolEntry]
    tool_citations: dict[str, dict[str, Any]]
    last_error: str | None
    auto_enroll_new_workspaces: bool
    # Admin-only: auth method used when the org grant was minted; None when no org grant exists.
    org_grant_auth_method: Literal["oauth", "static"] | None = None


class AdminCatalogRowOut(BaseModel):
    """One row in the admin template catalog."""

    template: MCPTemplateOut
    connector: MCPConnectorFactsOut | None
    disabled: bool
    in_use: bool
    needs_attention: bool
    enabled_workspace_count: int
    eligible_workspace_count: int
    org_grant_status: Literal["valid", "expired"] | None


class AdminCatalogListOut(BaseModel):
    """Envelope for admin catalog list."""

    items: list[AdminCatalogRowOut]


class WorkspaceCatalogRowOut(BaseModel):
    """One row in a workspace's template catalog."""

    template: MCPTemplateOut
    connector: MCPConnectorFactsOut | None
    enabled: bool
    usable: bool | None  # None when no connector/state yet
    reason: str | None
    credential_availability_by_scope: dict[Literal["org", "workspace", "user"], bool]


class WorkspaceCatalogListOut(BaseModel):
    """Envelope for workspace catalog list."""

    items: list[WorkspaceCatalogRowOut]


class CreateTemplateIn(BaseModel):
    """Body for creating a custom MCP template (admin org-custom, ws-custom).

    Cross-field validation: policy 'none' ⟺ auth 'none' (a connector with no
    auth method cannot have a credential slot, and vice versa).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    server_url: str
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["oauth", "static", "none"]
    default_credential_policy: Literal["org", "workspace", "user", "none"]

    @model_validator(mode="after")
    def _validate_policy_vs_auth(self) -> "CreateTemplateIn":
        if self.default_credential_policy == "none" and self.auth_method != "none":
            raise ValueError(
                "default_credential_policy='none' is only valid when auth_method='none'"
            )
        if self.auth_method == "none" and self.default_credential_policy != "none":
            raise ValueError("auth_method='none' requires default_credential_policy='none'")
        return self


class DistributeIn(BaseModel):
    """Distribution payload for template/connector enablement."""

    enable_existing: bool = True
    auto_enroll: bool = True


class TemplateStateIn(BaseModel):
    """Body for workspace enable/disable of a template."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    credential_policy: Literal["org", "workspace", "user"] | None = None
