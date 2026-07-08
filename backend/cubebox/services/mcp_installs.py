"""Connector / workspace-state / credential-grant service primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, server_url_hash, slugify_for_namespace
from cubebox.models import MCPConnector, MCPConnectorTemplate, MCPCredentialGrant
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.repositories.workspace import WorkspaceRepository
from cubebox.services.credential import CredentialService


@dataclass(frozen=True)
class MCPConnectorDefaults:
    """Derived defaults applied to a connector row."""

    auth_status: str
    credential_policy: str


@dataclass(frozen=True)
class ConnectorWithIdentity:
    """A connector plus its primary identity.

    ``install`` is kept as a temporary alias while route/schema call sites are
    migrated in this task.
    """

    connector: MCPConnector
    connector_id: str

    @property
    def install(self) -> MCPConnector:
        return self.connector


def install_defaults_for_auth_method(
    auth_method: str,
    requested_policy: str,
) -> MCPConnectorDefaults:
    if auth_method == "none":
        return MCPConnectorDefaults(auth_status="not_required", credential_policy="none")
    return MCPConnectorDefaults(auth_status="pending", credential_policy=requested_policy)


class MCPConnectorService:
    """Service-level orchestration for connector / state / grant writes."""

    def __init__(
        self,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        cred_service: CredentialService,
        *,
        org_id: str,
        actor_user_id: str,
        workspace_repo: WorkspaceRepository | None = None,
        connector_repo: MCPConnectorRepository,
    ) -> None:
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._cred_service = cred_service
        self._org_id = org_id
        self._actor_user_id = actor_user_id
        self._workspace_repo = workspace_repo
        self._connector_repo = connector_repo
        self._install_repo = connector_repo

    async def create_from_template_for_workspace(
        self,
        *,
        template: MCPConnectorTemplate,
        workspace_id: str,
        auth_method: str,
        credential_policy: str,
    ) -> ConnectorWithIdentity:
        if auth_method not in template.supported_auth_methods:
            raise ValueError("auth_method_not_supported_by_template")
        connector = await self._ensure_connector_from_template(
            template,
            auth_method=auth_method,
            credential_policy=credential_policy,
            auto_enroll_new_workspaces=False,
        )
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        await self._state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            connector_id=connector.id,
            enabled=True,
            credential_policy=defaults.credential_policy,
            enablement_source="workspace_manual",
            updated_by_user_id=self._actor_user_id,
        )
        return ConnectorWithIdentity(connector=connector, connector_id=connector.id)

    async def create_from_template_for_org(
        self,
        *,
        template: MCPConnectorTemplate,
        auth_method: str,
        credential_policy: str,
        distribution: dict[str, Any],
    ) -> ConnectorWithIdentity:
        if auth_method not in template.supported_auth_methods:
            raise ValueError("auth_method_not_supported_by_template")
        workspace_ids, enablement_source, mode = await self._resolve_distribution(distribution)
        connector = await self._ensure_connector_from_template(
            template,
            auth_method=auth_method,
            credential_policy=credential_policy,
            auto_enroll_new_workspaces=mode == "all",
        )
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        await self._fan_out_state_rows(
            connector=connector,
            workspace_ids=workspace_ids,
            credential_policy=defaults.credential_policy,
            enablement_source=enablement_source,
        )
        return ConnectorWithIdentity(connector=connector, connector_id=connector.id)

    async def create_custom_install_for_org(
        self,
        *,
        name: str,
        server_url: str,
        transport: str,
        auth_method: str,
        default_credential_policy: str,
        headers: dict[str, str] | None,
        distribution: dict[str, Any],
    ) -> ConnectorWithIdentity:
        workspace_ids, enablement_source, mode = await self._resolve_distribution(distribution)
        existing = await self._connector_repo.get_active_by_identity(
            template_id=None,
            server_url_hash=server_url_hash(server_url),
            slug_name=slugify_for_namespace(name),
        )
        if existing is not None:
            raise ValueError("install_already_exists")
        defaults = install_defaults_for_auth_method(auth_method, default_credential_policy)
        connector = await self._connector_repo.add(
            MCPConnector(
                org_id=self._org_id,
                template_id=None,
                name=name,
                server_url=server_url,
                server_url_hash=server_url_hash(server_url),
                transport=transport,
                auth_method=auth_method,
                default_credential_policy=defaults.credential_policy,
                auth_status=defaults.auth_status,
                headers=dict(headers or {}),
                auto_enroll_new_workspaces=mode == "all",
                status="active",
                created_by_user_id=self._actor_user_id,
            )
        )
        await self._fan_out_state_rows(
            connector=connector,
            workspace_ids=workspace_ids,
            credential_policy=defaults.credential_policy,
            enablement_source=enablement_source,
        )
        return ConnectorWithIdentity(connector=connector, connector_id=connector.id)

    async def _ensure_connector_from_template(
        self,
        template: MCPConnectorTemplate,
        *,
        auth_method: str,
        credential_policy: str,
        auto_enroll_new_workspaces: bool,
    ) -> MCPConnector:
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        existing = await self._connector_repo.get_active_by_identity(
            template_id=template.id,
            server_url_hash=server_url_hash(template.server_url),
            slug_name=slugify_for_namespace(template.name),
        )
        if existing is not None:
            existing.auth_method = auth_method
            existing.default_credential_policy = defaults.credential_policy
            existing.auth_status = defaults.auth_status
            existing.tool_citations = dict(template.tool_citation_defaults)
            existing.static_auth_style = template.static_auth_style
            existing.static_auth_header_name = template.static_auth_header_name
            existing.static_auth_query_param = template.static_auth_query_param
            existing.auto_enroll_new_workspaces = auto_enroll_new_workspaces
            return await self._connector_repo.update(existing)
        return await self._connector_repo.add(
            MCPConnector(
                org_id=self._org_id,
                template_id=template.id,
                name=template.name,
                server_url=template.server_url,
                server_url_hash=server_url_hash(template.server_url),
                transport=template.transport,
                auth_method=auth_method,
                default_credential_policy=defaults.credential_policy,
                auth_status=defaults.auth_status,
                oauth_client_config={},
                static_auth_style=template.static_auth_style,
                static_auth_header_name=template.static_auth_header_name,
                static_auth_query_param=template.static_auth_query_param,
                tool_citations=dict(template.tool_citation_defaults),
                auto_enroll_new_workspaces=auto_enroll_new_workspaces,
                created_by_user_id=self._actor_user_id,
            )
        )

    async def _resolve_distribution(
        self,
        distribution: dict[str, Any],
    ) -> tuple[list[str], str, str]:
        mode = distribution.get("mode")
        if mode not in {"all", "selected", "none"}:
            raise ValueError(f"unknown distribution mode: {mode!r}")

        if mode == "all":
            if self._workspace_repo is None:
                raise RuntimeError("distribution mode='all' requires workspace_repo")
            workspaces = await self._workspace_repo.list_for_org(self._org_id)
            return [ws.id for ws in workspaces], "admin_auto", "all"
        if mode == "selected":
            raw_ids = distribution.get("workspace_ids") or []
            if not isinstance(raw_ids, list):
                raise ValueError("distribution.workspace_ids must be a list")
            requested = [str(wid) for wid in raw_ids]
            if requested:
                if self._workspace_repo is None:
                    raise RuntimeError("distribution mode='selected' requires workspace_repo")
                valid_ws = await self._workspace_repo.list_for_org(self._org_id)
                valid_ids = {ws.id for ws in valid_ws}
                if any(wid not in valid_ids for wid in requested):
                    raise ValueError("workspace_not_in_org")
            return requested, "admin_manual", "selected"
        return [], "", "none"

    async def _fan_out_state_rows(
        self,
        *,
        connector: MCPConnector,
        workspace_ids: list[str],
        credential_policy: str,
        enablement_source: str,
    ) -> None:
        for ws_id in workspace_ids:
            await self._state_repo.upsert_for_connector(
                workspace_id=ws_id,
                connector_id=connector.id,
                enabled=True,
                credential_policy=credential_policy,
                enablement_source=enablement_source,
                updated_by_user_id=self._actor_user_id,
            )

    async def promote_workspace_install_to_org(
        self,
        *,
        connector_id: str,
        distribution: dict[str, Any],
    ) -> MCPConnector:
        connector = await self._require_active_connector(connector_id)
        workspace_ids, enablement_source, mode = await self._resolve_distribution(distribution)
        connector.auto_enroll_new_workspaces = mode == "all"
        saved = await self._connector_repo.update(connector)
        await self._fan_out_state_rows(
            connector=saved,
            workspace_ids=workspace_ids,
            credential_policy=saved.default_credential_policy,
            enablement_source=enablement_source,
        )
        return saved

    async def _connector_id_for_install(self, connector: MCPConnector) -> str | None:
        return connector.id

    async def _has_install_conflict(
        self,
        *,
        server_url_hash: str,
        name: str,
        template_id: str | None,
        exclude_id: str | None,
    ) -> bool:
        existing = await self._connector_repo.get_active_by_identity(
            template_id=template_id,
            server_url_hash=server_url_hash,
            slug_name=slugify_for_namespace(name),
        )
        return existing is not None and existing.id != exclude_id

    @staticmethod
    def _validate_grant_scope_shape(
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> None:
        if grant_scope == "org":
            if workspace_id is not None or user_id is not None:
                raise ValueError("grant_scope='org' must have workspace_id=None and user_id=None")
        elif grant_scope == "workspace":
            if workspace_id is None or user_id is not None:
                raise ValueError(
                    "grant_scope='workspace' requires workspace_id and forbids user_id"
                )
        elif grant_scope == "user":
            if workspace_id is None or user_id is None:
                raise ValueError("grant_scope='user' requires both workspace_id and user_id")
        else:
            raise ValueError(f"unknown grant_scope: {grant_scope!r}")

    async def _require_active_connector(self, connector_id: str) -> MCPConnector:
        connector = await self._connector_repo.get(connector_id)
        if connector is None or connector.org_id != self._org_id:
            raise ValueError("connector_not_found")
        if connector.status != "active":
            raise ValueError("connector_not_active")
        return connector

    async def create_static_grant(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        plaintext: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
    ) -> MCPCredentialGrant:
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        connector = await self._require_active_connector(connector_id)
        if connector.auth_method != "static":
            raise ValueError("static_grant_only_valid_for_static_auth")

        credential_name = name or f"mcp:{connector_id}:{grant_scope}"
        credential_id = await self._cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name,
            plaintext=plaintext,
        )
        existing = await self._grant_repo.get_for_connector_scope(
            connector_id=connector_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        if existing is None:
            return await self._grant_repo.add(
                MCPCredentialGrant(
                    org_id=self._org_id,
                    connector_id=connector_id,
                    grant_scope=grant_scope,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    credential_id=credential_id,
                    grant_status="valid",
                    created_by_user_id=self._actor_user_id,
                )
            )
        existing.connector_id = connector_id
        existing.credential_id = credential_id
        existing.refresh_credential_id = None
        existing.expires_at = None
        existing.grant_status = "valid"
        return await self._grant_repo.update(existing)

    async def disconnect_grant(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        deleted = await self._grant_repo.delete_scope(
            connector_id,
            grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        for grant in deleted:
            for cred_id in (grant.credential_id, grant.refresh_credential_id):
                if not cred_id:
                    continue
                try:
                    await self._cred_service.delete(credential_id=cred_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("MCP disconnect: skipping vault delete for {}: {}", cred_id, exc)
        if grant_scope == "org" and deleted:
            connector = await self._connector_repo.get(connector_id)
            if connector is not None and connector.discovery_status == "error":
                connector.discovery_status = "not_run"
                connector.last_error = None
                await self._connector_repo.update(connector)

    async def uninstall(self, connector_id: str) -> MCPConnector:
        connector = await self._connector_repo.get(connector_id)
        if connector is None:
            raise ValueError(f"connector not found: {connector_id}")
        await self._state_repo.delete_for_connector(connector_id)
        await self._grant_repo.delete_for_connector(connector_id)
        connector.status = "uninstalled"
        connector.auth_status = "disconnected"
        connector.updated_at = datetime.now(UTC)
        return await self._connector_repo.update(connector)
