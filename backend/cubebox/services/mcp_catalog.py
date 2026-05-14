"""MCP catalog service: list, install, delete, switch auth method.

Read path:
- ``list_for_member`` returns each active catalog connector + 3 install
  status fields scoped to the calling workspace + user.

Write path (admin):
- ``install_for_org``: create one org-wide ``mcp_servers`` row tied to a
  catalog connector. Static → write credential and refresh tools. OAuth
  → leave ``authed=false`` and return ``requires_oauth=True`` so the API
  layer can hand off to the OAuth start route. None → ``authed=true``
  immediately. The admin endpoint is org-wide only — user-scope installs
  go through ``install_for_workspace``.
- ``delete_install``: soft-disable (clears credentials, ``authed=false``,
  best-effort OAuth revoke stub). Keeps the row for history.
- ``switch_auth_method``: re-key flow.

Write path (workspace user):
- ``install_for_workspace``: forces ``scope=user`` (workspace-private,
  ``credential_scope=user``).

DTOs are local to this module — Phase 3 owns the API schemas.
"""

from __future__ import annotations

import copy
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from cubebox.auth.context import RequestContext
from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, server_url_hash
from cubebox.mcp.cubepi_admin_discovery import discover_tools_metadata as discover_tools
from cubebox.mcp.exceptions import (
    MCPCatalogAuthMethodUnsupported,
    MCPCatalogConnectorNotFound,
    MCPCatalogInstallExists,
    MCPCredentialRequired,
    MCPServerNotFound,
    MCPUserScopeCredentialForbidden,
)
from cubebox.models import (
    MCPCatalogConnector,
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPCredential,
)
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService

_VALID_AUTH_METHODS = {"static", "oauth", "none"}


@dataclass(frozen=True)
class CatalogConnectorDTO:
    """Catalog connector + per-(workspace, user) install status."""

    connector: MCPCatalogConnector
    org_install_id: str | None
    workspace_visible: bool
    user_install_id: str | None


@dataclass(frozen=True)
class InstallResult:
    """Outcome of an install / re-key call."""

    install_id: str
    requires_oauth: bool


class MCPCatalogService:
    """Application service for the catalog read + install lifecycle."""

    def __init__(
        self,
        *,
        catalog_repo: MCPCatalogConnectorRepository,
        server_repo: MCPServerRepository,
        ws_cred_repo: WorkspaceMCPCredentialRepository,
        user_cred_repo: UserMCPCredentialRepository,
        override_repo: WorkspaceMCPOverrideRepository,
        cred_service: CredentialService,
        request_context: RequestContext,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.server_repo = server_repo
        self.ws_cred_repo = ws_cred_repo
        self.user_cred_repo = user_cred_repo
        self.override_repo = override_repo
        self.cred_service = cred_service
        self._ctx = request_context

    # ---------------- read path ---------------- #

    async def list_for_member(
        self,
        workspace_id: str,
        *,
        q: str | None = None,
        provider: str | None = None,
    ) -> list[CatalogConnectorDTO]:
        """List active catalog connectors with install status for ``workspace_id``."""
        connectors = await self.catalog_repo.list_active()

        # Pre-load all servers for this org once and bucket them so we
        # don't issue N round-trips across the loop below.
        servers = await self.server_repo.list_for_org()
        org_installs_by_catalog: dict[str, MCPServer] = {}
        ws_installs_by_catalog: dict[str, list[MCPServer]] = {}
        for server in servers:
            if server.catalog_connector_id is None:
                continue
            if server.owner_workspace_id is None:
                org_installs_by_catalog[server.catalog_connector_id] = server
            else:
                ws_installs_by_catalog.setdefault(server.catalog_connector_id, []).append(server)

        # Workspace overrides — enabled=True rows make an org install visible.
        ws_overrides = await self.override_repo.list_for_workspace(workspace_id)
        enabled_server_ids = {row.mcp_server_id for row in ws_overrides if row.enabled is True}

        result: list[CatalogConnectorDTO] = []
        q_lower = q.lower() if q else None

        for connector in connectors:
            if provider is not None and connector.provider != provider:
                continue
            if q_lower is not None:
                haystack = (connector.name + " " + connector.description).lower()
                if q_lower not in haystack:
                    continue

            org_install = org_installs_by_catalog.get(connector.id)
            org_install_id = org_install.id if org_install is not None else None

            # Visible only if explicitly enabled for this workspace.
            workspace_visible = (
                org_install is not None
                and org_install.authed
                and org_install.id in enabled_server_ids
            )

            user_install_id: str | None = None
            for ws_server in ws_installs_by_catalog.get(connector.id, []):
                if ws_server.owner_workspace_id != workspace_id:
                    continue
                if ws_server.created_by_user_id != self._ctx.user.id:
                    continue
                user_install_id = ws_server.id
                if ws_server.authed:
                    workspace_visible = True
                break

            result.append(
                CatalogConnectorDTO(
                    connector=connector,
                    org_install_id=org_install_id,
                    workspace_visible=workspace_visible,
                    user_install_id=user_install_id,
                )
            )
        return result

    # ---------------- write path: org admin ---------------- #

    async def install_for_org(
        self,
        *,
        catalog_id: str,
        auth_method: str,
        credential_plaintext: str | None,
        credential_name: str | None,
        auto_enable_workspaces: bool,
    ) -> InstallResult:
        """Org admin install — always org-wide (visible to all workspaces).

        User-scope installs go through ``install_for_workspace`` (the
        ``/api/v1/ws/{ws}/mcp/catalog/{id}/install`` endpoint). The admin
        endpoint never writes a workspace-private row.
        """
        connector = await self._get_connector_or_raise(catalog_id)
        self._assert_auth_method_supported(connector, auth_method)

        # NOTE: With inverted semantics (no override row = invisible),
        # ``auto_enable_workspaces`` would need to create explicit enabled
        # override rows for each workspace. Deferred to a follow-up task;
        # for now the flag is accepted but not acted on.
        del auto_enable_workspaces

        return await self._install_org_wide(
            connector=connector,
            auth_method=auth_method,
            credential_plaintext=credential_plaintext,
            credential_name=credential_name,
        )

    async def install_for_workspace(
        self,
        *,
        catalog_id: str,
        workspace_id: str,
        auth_method: str,
        credential_plaintext: str | None,
        credential_name: str | None,
    ) -> InstallResult:
        """Workspace user self-install. Always ``scope=user`` (workspace-private)."""
        connector = await self._get_connector_or_raise(catalog_id)
        self._assert_auth_method_supported(connector, auth_method)
        return await self._install_workspace_user(
            connector=connector,
            workspace_id=workspace_id,
            auth_method=auth_method,
            credential_plaintext=credential_plaintext,
            credential_name=credential_name,
        )

    async def delete_install(self, install_id: str) -> None:
        """Soft disable: clear credentials, mark unauthed, keep the row.

        Does NOT call OAuth revoke — that's a Phase 5 concern. The TODO
        notes the entry point so the OAuth route can wire it in later.
        """
        server = await self.server_repo.get(install_id)
        if server is None:
            raise MCPServerNotFound(install_id)

        # TODO(phase-5-oauth): if server.auth_method == "oauth", call
        # OAuthTokenManager.revoke(server) before clearing the credential
        # rows so the AS forgets the grant.

        # Clear scope-specific credentials.
        if server.credential_scope == "org" and server.credential_id is not None:
            cred_id = server.credential_id
            server.credential_id = None
            with suppress(CredentialNotFound):
                await self.cred_service.delete(credential_id=cred_id)
        elif server.credential_scope == "workspace":
            for ws_cred in await self.ws_cred_repo.list_for_server(server.id):
                await self.ws_cred_repo.delete(
                    workspace_id=ws_cred.workspace_id,
                    mcp_server_id=server.id,
                )
                with suppress(CredentialNotFound):
                    await self.cred_service.delete(credential_id=ws_cred.credential_id)
        elif server.credential_scope == "user":
            for user_cred in await self.user_cred_repo.list_for_server(server.id):
                await self.user_cred_repo.delete(
                    user_id=user_cred.user_id,
                    mcp_server_id=server.id,
                )
                with suppress(CredentialNotFound):
                    await self.cred_service.delete(credential_id=user_cred.credential_id)
                if user_cred.oauth_refresh_token_credential_id is not None:
                    with suppress(CredentialNotFound):
                        await self.cred_service.delete(
                            credential_id=user_cred.oauth_refresh_token_credential_id
                        )

        # Clear oauth client config (refresh token credential id, expires_at).
        server.oauth_client_config = {}
        server.authed = False
        server.last_error = None
        server.tools_cache = []
        server.last_discovered_at = datetime.now(UTC)
        await self.server_repo.update(server)

    async def switch_auth_method(
        self,
        *,
        install_id: str,
        new_auth_method: str,
        credential_plaintext: str | None,
        credential_name: str | None = None,
    ) -> InstallResult:
        """Re-key flow: change a server's auth_method, rewriting credentials.

        ``credential_name``, if provided, is used as the new credential's
        display name; otherwise we auto-generate a scope-keyed name.
        """
        server = await self.server_repo.get(install_id)
        if server is None:
            raise MCPServerNotFound(install_id)
        if server.catalog_connector_id is None:
            raise MCPCatalogConnectorNotFound(f"server {install_id} has no catalog_connector_id")
        connector = await self.catalog_repo.get_by_id(server.catalog_connector_id)
        if connector is None:
            raise MCPCatalogConnectorNotFound(server.catalog_connector_id)
        self._assert_auth_method_supported(connector, new_auth_method)

        # Clear out existing creds first (best-effort; mirror delete_install).
        await self.delete_install(install_id)
        # delete_install set authed=False; refresh server reference.
        server = await self.server_repo.get(install_id)
        assert server is not None  # re-fetched above

        server.auth_method = new_auth_method
        if new_auth_method == "static":
            if not credential_plaintext:
                raise MCPCredentialRequired()
            if server.credential_scope == "org":
                cred_id = await self.cred_service.create(
                    kind=CREDENTIAL_KIND_MCP,
                    name=credential_name or f"mcp:{server.name}:org",
                    plaintext=credential_plaintext,
                )
                server.credential_id = cred_id
            elif server.credential_scope == "workspace":
                if server.owner_workspace_id is None:
                    raise ValueError(
                        f"server {server.id} has credential_scope=workspace but no "
                        f"owner_workspace_id"
                    )
                cred_id = await self.cred_service.create(
                    kind=CREDENTIAL_KIND_MCP,
                    name=credential_name or f"mcp:{server.name}:ws:{server.owner_workspace_id}",
                    plaintext=credential_plaintext,
                )
                # Recreate the workspace_mcp_credentials row.
                await self.ws_cred_repo.add(
                    WorkspaceMCPCredential(
                        org_id=self._ctx.org_id,
                        workspace_id=server.owner_workspace_id,
                        mcp_server_id=server.id,
                        credential_id=cred_id,
                        created_by_user_id=self._ctx.user.id,
                    )
                )
            elif server.credential_scope == "user":
                cred_id = await self.cred_service.create(
                    kind=CREDENTIAL_KIND_MCP,
                    name=credential_name or f"mcp:{server.name}:user:{self._ctx.user.id}",
                    plaintext=credential_plaintext,
                )
                # Recreate the user_mcp_credentials row.
                await self.user_cred_repo.add(
                    UserMCPCredential(
                        org_id=self._ctx.org_id,
                        user_id=self._ctx.user.id,
                        mcp_server_id=server.id,
                        credential_id=cred_id,
                    )
                )
            else:
                raise ValueError(
                    f"unsupported credential_scope {server.credential_scope!r} "
                    f"for switch_auth_method"
                )
            await self.server_repo.update(server)
            await self._refresh_tools(server, plaintext=credential_plaintext)
            return InstallResult(install_id=server.id, requires_oauth=False)

        if new_auth_method == "oauth":
            if credential_plaintext is not None:
                raise MCPUserScopeCredentialForbidden(
                    "auth_method=oauth must not carry a static credential"
                )
            await self.server_repo.update(server)
            return InstallResult(install_id=server.id, requires_oauth=True)

        # none
        if credential_plaintext is not None:
            raise MCPUserScopeCredentialForbidden("auth_method=none must not carry a credential")
        await self.server_repo.update(server)
        await self._refresh_tools(server, plaintext=None)
        return InstallResult(install_id=server.id, requires_oauth=False)

    # ---------------- private helpers ---------------- #

    async def _install_org_wide(
        self,
        *,
        connector: MCPCatalogConnector,
        auth_method: str,
        credential_plaintext: str | None,
        credential_name: str | None,
    ) -> InstallResult:
        await self._reject_duplicate(
            connector_id=connector.id,
            owner_workspace_id=None,
        )
        credential_scope, credential_id = await self._materialize_credential(
            connector=connector,
            auth_method=auth_method,
            credential_scope_default="org",
            credential_plaintext=credential_plaintext,
            credential_name=credential_name,
            owner_workspace_id=None,
        )

        server = await self.server_repo.add(
            MCPServer(
                org_id=self._ctx.org_id,
                owner_workspace_id=None,
                catalog_connector_id=connector.id,
                name=self._compose_name(connector, owner_workspace_id=None),
                server_url=connector.server_url,
                server_url_hash=server_url_hash(connector.server_url),
                transport=connector.transport,
                auth_method=auth_method,
                credential_scope=credential_scope,
                credential_id=credential_id,
                headers={},
                timeout=30.0,
                sse_read_timeout=300.0,
                tool_citations=copy.deepcopy(connector.tool_citations or {}),
                created_by_user_id=self._ctx.user.id,
            )
        )

        return await self._finalize_install(
            server=server,
            auth_method=auth_method,
            credential_plaintext=credential_plaintext,
        )

    async def _install_workspace_user(
        self,
        *,
        connector: MCPCatalogConnector,
        workspace_id: str,
        auth_method: str,
        credential_plaintext: str | None,
        credential_name: str | None,
    ) -> InstallResult:
        await self._reject_duplicate(
            connector_id=connector.id,
            owner_workspace_id=workspace_id,
        )
        # Workspace path forces scope=user. credential is materialized
        # *after* the server row exists so user_mcp_credentials can FK to it.
        if auth_method == "static" and not credential_plaintext:
            raise MCPCredentialRequired()
        if auth_method == "oauth" and credential_plaintext is not None:
            raise MCPUserScopeCredentialForbidden(
                "auth_method=oauth must not carry a static credential"
            )
        if auth_method == "none" and credential_plaintext is not None:
            raise MCPUserScopeCredentialForbidden("auth_method=none must not carry a credential")

        server = await self.server_repo.add(
            MCPServer(
                org_id=self._ctx.org_id,
                owner_workspace_id=workspace_id,
                catalog_connector_id=connector.id,
                name=self._compose_name(connector, owner_workspace_id=workspace_id),
                server_url=connector.server_url,
                server_url_hash=server_url_hash(connector.server_url),
                transport=connector.transport,
                auth_method=auth_method,
                credential_scope="user",
                credential_id=None,
                headers={},
                timeout=30.0,
                sse_read_timeout=300.0,
                tool_citations=copy.deepcopy(connector.tool_citations or {}),
                created_by_user_id=self._ctx.user.id,
            )
        )

        if auth_method == "static":
            assert credential_plaintext is not None
            cred_id = await self.cred_service.create(
                kind=CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{connector.slug}:user:{self._ctx.user.id}",
                plaintext=credential_plaintext,
            )
            await self.user_cred_repo.add(
                UserMCPCredential(
                    org_id=self._ctx.org_id,
                    user_id=self._ctx.user.id,
                    mcp_server_id=server.id,
                    credential_id=cred_id,
                )
            )

        return await self._finalize_install(
            server=server,
            auth_method=auth_method,
            credential_plaintext=credential_plaintext,
        )

    async def _materialize_credential(
        self,
        *,
        connector: MCPCatalogConnector,
        auth_method: str,
        credential_scope_default: str,
        credential_plaintext: str | None,
        credential_name: str | None,
        owner_workspace_id: str | None,
    ) -> tuple[str, str | None]:
        """Decide ``(credential_scope, credential_id)`` for org-wide installs.

        Returns the scope to write on the ``MCPServer`` row plus an
        already-persisted credential id (or None for oauth/none paths).
        """
        if auth_method == "none":
            return ("none", None)
        if auth_method == "oauth":
            return (credential_scope_default, None)
        # static
        if not credential_plaintext:
            raise MCPCredentialRequired()
        cred_id = await self.cred_service.create(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name or f"mcp:{connector.slug}:org",
            plaintext=credential_plaintext,
        )
        return (credential_scope_default, cred_id)

    async def _finalize_install(
        self,
        *,
        server: MCPServer,
        auth_method: str,
        credential_plaintext: str | None,
    ) -> InstallResult:
        if auth_method == "oauth":
            # Phase 5 will run the OAuth dance; until then leave authed=false.
            return InstallResult(install_id=server.id, requires_oauth=True)
        if auth_method == "none":
            await self._refresh_tools(server, plaintext=None)
            return InstallResult(install_id=server.id, requires_oauth=False)
        # static
        await self._refresh_tools(server, plaintext=credential_plaintext)
        return InstallResult(install_id=server.id, requires_oauth=False)

    async def _refresh_tools(self, server: MCPServer, *, plaintext: str | None) -> None:
        """Best-effort tool discovery; failures mark the server unauthenticated."""
        success, tools, error = await discover_tools(server, credential_or_token=plaintext)
        server.authed = success
        server.tools_cache = tools or []
        server.last_error = None if success else error
        server.last_discovered_at = datetime.now(UTC)
        await self.server_repo.update(server)

    async def _reject_duplicate(
        self,
        *,
        connector_id: str,
        owner_workspace_id: str | None,
    ) -> None:
        """Surface a clean error before the partial unique index trips on commit."""
        servers = await self.server_repo.list_for_org(owner_workspace_id=owner_workspace_id)
        for server in servers:
            if server.catalog_connector_id == connector_id:
                raise MCPCatalogInstallExists(
                    f"install already exists for catalog={connector_id} "
                    f"workspace={owner_workspace_id}"
                )

    @staticmethod
    def _compose_name(
        connector: MCPCatalogConnector,
        *,
        owner_workspace_id: str | None,
    ) -> str:
        """Per-scope unique server name. Org-wide just uses the slug; workspace
        rows append the workspace id so the (org, workspace, name) uniqueness
        index doesn't collide across workspaces installing the same connector."""
        if owner_workspace_id is None:
            return f"catalog:{connector.slug}"
        return f"catalog:{connector.slug}:ws:{owner_workspace_id}"

    async def _get_connector_or_raise(self, catalog_id: str) -> MCPCatalogConnector:
        connector = await self.catalog_repo.get_by_id(catalog_id)
        if connector is None or connector.status != "active":
            raise MCPCatalogConnectorNotFound(catalog_id)
        return connector

    @staticmethod
    def _assert_auth_method_supported(
        connector: MCPCatalogConnector,
        auth_method: str,
    ) -> None:
        if auth_method not in _VALID_AUTH_METHODS:
            raise ValueError(f"unknown auth_method: {auth_method}")
        if auth_method not in connector.supported_auth_methods:
            raise MCPCatalogAuthMethodUnsupported(
                f"connector {connector.slug} does not support auth_method={auth_method}"
            )
