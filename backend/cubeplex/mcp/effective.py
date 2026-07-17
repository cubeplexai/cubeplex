"""Effective-state derivation + DB-backed service for MCP connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from cubeplex.mcp.exceptions import OAuthRefreshFailed
from cubeplex.mcp.oauth.token_manager import OAuthTokenManager
from cubeplex.models import (
    MCPConnector,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)
from cubeplex.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)

CredentialPolicy = Literal["org", "workspace", "user", "none"]

MCPEffectiveReason = Literal[
    "usable",
    "not_installed",
    "not_enabled_in_workspace",
    "install_uninstalled",
    "template_deprecated",
    "template_disabled_in_org",
    "pending_oauth",
    "missing_org_grant",
    "missing_workspace_grant",
    "user_needs_connection",
    "grant_expired",
    "discovery_failed",
    "server_unreachable",
]


@dataclass(frozen=True)
class MCPGrantInput:
    scope: str
    status: str
    has_refresh: bool
    auth_method: str


@dataclass(frozen=True)
class MCPEffectiveInput:
    template_status: str | None
    install_present: bool
    install_state: str
    workspace_state_present: bool
    workspace_enabled: bool
    org_disabled: bool
    auth_required: bool
    oauth_supported: bool
    discovery_status: str
    credential_policy: CredentialPolicy
    grant: MCPGrantInput | None
    transport: str


@dataclass(frozen=True)
class MCPEffectiveResult:
    usable: bool
    reason: MCPEffectiveReason
    credential_availability: Literal["available", "missing", "not_required"]


def _missing_grant_reason(policy: CredentialPolicy) -> MCPEffectiveReason:
    if policy == "org":
        return "missing_org_grant"
    if policy == "workspace":
        return "missing_workspace_grant"
    if policy == "user":
        return "user_needs_connection"
    return "missing_org_grant"


def compute_effective_state(value: MCPEffectiveInput) -> MCPEffectiveResult:
    if value.org_disabled:
        return MCPEffectiveResult(False, "template_disabled_in_org", "missing")
    if not value.install_present:
        return MCPEffectiveResult(False, "not_installed", "missing")
    if value.install_state == "uninstalled":
        return MCPEffectiveResult(False, "install_uninstalled", "missing")
    if value.template_status == "disabled":
        return MCPEffectiveResult(False, "template_deprecated", "missing")
    if not value.workspace_state_present or not value.workspace_enabled:
        return MCPEffectiveResult(False, "not_enabled_in_workspace", "missing")
    if not value.auth_required:
        return MCPEffectiveResult(True, "usable", "not_required")
    if value.grant is None:
        if value.oauth_supported and value.credential_policy in {"org", "workspace"}:
            return MCPEffectiveResult(False, "pending_oauth", "missing")
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")
    if value.grant.status == "expired":
        return MCPEffectiveResult(False, "grant_expired", "missing")
    if value.grant.scope != value.credential_policy:
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")
    if value.discovery_status == "error":
        return MCPEffectiveResult(False, "discovery_failed", "missing")
    return MCPEffectiveResult(True, "usable", "available")


@dataclass
class MCPEffectiveConnectorDTO:
    connector: MCPConnector
    template: MCPConnectorTemplate | None
    workspace_state: MCPWorkspaceConnectorState | None
    grant: MCPCredentialGrant | None
    credential_policy: CredentialPolicy
    required_grant_scope: str | None
    credential_availability: Literal["available", "missing", "not_required"]
    credential_source: Literal["org", "workspace", "user", "none"]
    credential_availability_by_scope: dict[Literal["org", "workspace", "user"], bool]
    usable: bool
    reason: MCPEffectiveReason
    template_status: str | None

    @property
    def install(self) -> MCPConnector:
        return self.connector


@dataclass
class MCPRuntimeConnectorSpec:
    connector_id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    grant_scope: str | None
    credential_id: str | None
    refresh_credential_id: str | None
    tool_citations: dict[str, dict[str, Any]]
    tools_cache: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0
    template_id: str | None = None
    org_id: str = ""
    workspace_id: str = ""
    grant: MCPCredentialGrant | None = None
    oauth_client_config: dict[str, Any] = field(default_factory=dict)
    discovery_metadata: dict[str, Any] = field(default_factory=dict)
    last_discovered_at: datetime | None = None
    static_auth_style: str = "bearer"
    static_auth_header_name: str | None = None
    static_auth_query_param: str | None = None


class MCPEffectiveConnectorService:
    """Compute effective state for every connector visible to a workspace."""

    def __init__(
        self,
        *,
        template_repo: MCPConnectorTemplateRepository,
        settings_repo: MCPTemplateSettingsRepository,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        org_id: str,
        connector_repo: MCPConnectorRepository | None = None,
        install_repo: MCPConnectorRepository | None = None,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self._template_repo = template_repo
        self._settings_repo = settings_repo
        resolved_connector_repo = connector_repo or install_repo
        if resolved_connector_repo is None:
            raise TypeError("connector_repo is required")
        self._connector_repo = resolved_connector_repo
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._org_id = org_id
        self._token_manager = token_manager

    async def list_for_workspace_user(
        self,
        workspace_id: str,
        user_id: str,
        *,
        include_unusable: bool = True,
        include_disabled_org_installs: bool = True,
    ) -> list[MCPEffectiveConnectorDTO]:
        rows = await self._collect_rows(
            workspace_id=workspace_id,
            user_id=user_id,
            include_disabled_org_installs=include_disabled_org_installs,
        )
        if include_unusable:
            return rows
        return [row for row in rows if row.usable]

    async def list_runtime_specs(
        self,
        workspace_id: str,
        user_id: str,
    ) -> list[MCPRuntimeConnectorSpec]:
        rows = await self.list_for_workspace_user(
            workspace_id,
            user_id,
            include_unusable=False,
        )
        return [
            MCPRuntimeConnectorSpec(
                connector_id=row.connector.id,
                name=row.connector.name,
                server_url=row.connector.server_url,
                transport=row.connector.transport,
                auth_method=row.grant.auth_method if row.grant is not None else "none",
                grant_scope=row.grant.grant_scope if row.grant is not None else None,
                credential_id=row.grant.credential_id if row.grant is not None else None,
                refresh_credential_id=(
                    row.grant.refresh_credential_id if row.grant is not None else None
                ),
                tool_citations=dict(row.connector.tool_citations or {}),
                tools_cache=list(row.connector.tools_cache or []),
                headers=dict(row.connector.headers or {}),
                timeout=row.connector.timeout,
                sse_read_timeout=row.connector.sse_read_timeout,
                template_id=row.connector.template_id,
                org_id=row.connector.org_id,
                workspace_id=workspace_id,
                grant=row.grant,
                oauth_client_config=dict(row.connector.oauth_client_config or {}),
                discovery_metadata=dict(row.connector.discovery_metadata or {}),
                last_discovered_at=row.connector.last_discovered_at,
                static_auth_style=row.connector.static_auth_style or "bearer",
                static_auth_header_name=row.connector.static_auth_header_name,
                static_auth_query_param=row.connector.static_auth_query_param,
            )
            for row in rows
        ]

    async def _collect_rows(
        self,
        *,
        workspace_id: str,
        user_id: str,
        include_disabled_org_installs: bool,
    ) -> list[MCPEffectiveConnectorDTO]:
        connectors = await self._connector_repo.list_active()
        states = await self._state_repo.list_for_workspace(workspace_id)
        states_by_connector = {state.connector_id: state for state in states}
        if include_disabled_org_installs:
            visible_ids = set(states_by_connector)
        else:
            visible_ids = {
                connector_id for connector_id, state in states_by_connector.items() if state.enabled
            }
        visible = [connector for connector in connectors if connector.id in visible_ids]
        if not visible:
            return []

        template_ids = {
            connector.template_id for connector in visible if connector.template_id is not None
        }
        templates_by_id: dict[str, MCPConnectorTemplate] = {}
        for template_id in template_ids:
            template = await self._template_repo.get(template_id)
            if template is not None:
                templates_by_id[template_id] = template

        # Load disabled template IDs once for the whole batch.
        disabled_ids = await self._settings_repo.disabled_template_ids()

        rows: list[MCPEffectiveConnectorDTO] = []
        for connector in visible:
            state = states_by_connector.get(connector.id)
            policy = (
                state.credential_policy
                if state is not None
                else connector.default_credential_policy
            )
            template = templates_by_id.get(connector.template_id)
            template_status = template.status if template is not None else None

            # Derive auth flags from the template (no longer from the connector row).
            methods = set(template.supported_auth_methods or []) if template is not None else set()
            auth_required = bool(methods - {"none"})
            oauth_supported = "oauth" in methods
            org_disabled = connector.template_id in disabled_ids

            grant = await self._resolve_grant(
                connector=connector,
                template=template,
                policy=policy,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            credential_availability_by_scope = await self._credential_availability_by_scope(
                connector=connector,
                auth_required=auth_required,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            grant_input = (
                MCPGrantInput(
                    scope=grant.grant_scope,
                    status=grant.grant_status,
                    has_refresh=grant.refresh_credential_id is not None,
                    auth_method=grant.auth_method,
                )
                if grant is not None
                else None
            )
            effective = compute_effective_state(
                MCPEffectiveInput(
                    template_status=template_status,
                    install_present=True,
                    install_state=connector.status,
                    workspace_state_present=state is not None,
                    workspace_enabled=state.enabled if state is not None else False,
                    org_disabled=org_disabled,
                    auth_required=auth_required,
                    oauth_supported=oauth_supported,
                    discovery_status=connector.discovery_status,
                    credential_policy=_cast_policy(policy),
                    grant=grant_input,
                    transport=connector.transport,
                )
            )
            rows.append(
                MCPEffectiveConnectorDTO(
                    connector=connector,
                    template=template,
                    workspace_state=state,
                    grant=grant,
                    credential_policy=_cast_policy(policy),
                    required_grant_scope=_required_scope_for(policy),
                    credential_availability=effective.credential_availability,
                    credential_source=_cast_source(policy),
                    credential_availability_by_scope=credential_availability_by_scope,
                    usable=effective.usable,
                    reason=effective.reason,
                    template_status=template_status,
                )
            )
        return rows

    async def _credential_availability_by_scope(
        self,
        *,
        connector: MCPConnector,
        auth_required: bool,
        workspace_id: str,
        user_id: str,
    ) -> dict[Literal["org", "workspace", "user"], bool]:
        if not auth_required:
            return {"org": False, "workspace": False, "user": False}
        org_grant = await self._grant_repo.get_for_connector_scope(
            connector_id=connector.id,
            grant_scope="org",
            workspace_id=None,
            user_id=None,
        )
        workspace_grant = await self._grant_repo.get_for_connector_scope(
            connector_id=connector.id,
            grant_scope="workspace",
            workspace_id=workspace_id,
            user_id=None,
        )
        user_grant = await self._grant_repo.get_for_connector_scope(
            connector_id=connector.id,
            grant_scope="user",
            workspace_id=workspace_id,
            user_id=user_id,
        )
        return {
            "org": org_grant is not None,
            "workspace": workspace_grant is not None,
            "user": user_grant is not None,
        }

    async def _resolve_grant(
        self,
        *,
        connector: MCPConnector,
        template: MCPConnectorTemplate | None,
        policy: str,
        workspace_id: str,
        user_id: str,
    ) -> MCPCredentialGrant | None:
        if policy == "none":
            return None
        grant = await self._grant_repo.get_for_connector_scope(
            connector_id=connector.id,
            grant_scope=policy,
            workspace_id=workspace_id if policy in {"workspace", "user"} else None,
            user_id=user_id if policy == "user" else None,
        )
        if grant is not None and grant.auth_method == "oauth" and self._token_manager is not None:
            try:
                await self._token_manager.get_access_token_for_grant(
                    grant=grant,
                    grant_repo=self._grant_repo,
                    server_url=connector.server_url,
                    oauth_client_config=dict(connector.oauth_client_config or {}),
                )
            except OAuthRefreshFailed:
                grant.grant_status = "expired"
                await self._grant_repo.update(grant)
        return grant


def _cast_policy(value: str) -> CredentialPolicy:
    if value in {"org", "workspace", "user", "none"}:
        return value  # type: ignore[return-value]
    return "org"


def _cast_source(value: str) -> Literal["org", "workspace", "user", "none"]:
    if value in {"org", "workspace", "user", "none"}:
        return value  # type: ignore[return-value]
    return "org"


def _required_scope_for(policy: str) -> str | None:
    return None if policy == "none" else policy
