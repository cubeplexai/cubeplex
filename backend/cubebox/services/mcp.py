"""MCP connector service: CRUD, invariants, credential wiring, and discovery."""

from contextlib import suppress
from typing import Any

from cubebox.auth.context import RequestContext
from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP,
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    server_url_hash,
)
from cubebox.mcp.discovery import discover_tools
from cubebox.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoOverride,
    OAuthInvalidServerState,
    OAuthRefreshContention,
    OAuthRefreshFailed,
)
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.models import (
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
from cubebox.services.credential import CredentialService

_VALID_SCOPES = {"org", "workspace", "user", "none"}
_VALID_METHODS = {"static", "oauth", "none"}
_VALID_TRANSPORTS = {"streamable_http", "sse"}


class MCPServerService:
    """Application service for DB-backed MCP connectors."""

    def __init__(
        self,
        *,
        server_repo: MCPServerRepository,
        ws_cred_repo: WorkspaceMCPCredentialRepository,
        user_cred_repo: UserMCPCredentialRepository,
        override_repo: WorkspaceMCPOverrideRepository,
        cred_service: CredentialService,
        request_context: RequestContext,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self.server_repo = server_repo
        self.ws_cred_repo = ws_cred_repo
        self.user_cred_repo = user_cred_repo
        self.override_repo = override_repo
        self.cred_service = cred_service
        self._ctx = request_context
        self._token_manager = token_manager

    async def create(
        self,
        *,
        name: str,
        server_url: str,
        transport: str,
        auth_method: str,
        credential_scope: str,
        credential_plaintext: str | None = None,
        credential_name: str | None = None,
        owner_workspace_id: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        sse_read_timeout: float = 300.0,
    ) -> MCPServer:
        self._validate_create_invariants(
            transport=transport,
            auth_method=auth_method,
            credential_scope=credential_scope,
            credential_plaintext=credential_plaintext,
            owner_workspace_id=owner_workspace_id,
        )
        await self._ensure_unique_name_and_url(
            name=name,
            server_url=server_url,
            owner_workspace_id=owner_workspace_id,
        )

        credential_id: str | None = None
        if credential_scope == "org" and auth_method == "static":
            assert credential_plaintext is not None  # invariant guard above
            credential_id = await self.cred_service.create(
                kind=CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{name}:org",
                plaintext=credential_plaintext,
            )

        server = await self.server_repo.add(
            MCPServer(
                org_id=self._ctx.org_id,
                owner_workspace_id=owner_workspace_id,
                name=name,
                server_url=server_url,
                server_url_hash=server_url_hash(server_url),
                transport=transport,
                auth_method=auth_method,
                credential_scope=credential_scope,
                credential_id=credential_id,
                headers=headers or {},
                timeout=timeout,
                sse_read_timeout=sse_read_timeout,
                created_by_user_id=self._ctx.user.id,
            )
        )

        if credential_scope == "workspace":
            if credential_plaintext is None or owner_workspace_id is None:
                raise MCPCredentialRequired()
            workspace_credential_id = await self.cred_service.create(
                kind=CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{name}:ws:{owner_workspace_id}",
                plaintext=credential_plaintext,
            )
            await self.ws_cred_repo.add(
                WorkspaceMCPCredential(
                    org_id=self._ctx.org_id,
                    workspace_id=owner_workspace_id,
                    mcp_server_id=server.id,
                    credential_id=workspace_credential_id,
                    created_by_user_id=self._ctx.user.id,
                )
            )

        await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    async def update(
        self,
        *,
        server_id: str,
        name: str | None = None,
        server_url: str | None = None,
        transport: str | None = None,
        credential_plaintext: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)

        if name is not None and name != server.name:
            existing_servers = await self.server_repo.list_for_org(
                owner_workspace_id=server.owner_workspace_id
            )
            if any(
                existing.id != server.id and existing.name == name for existing in existing_servers
            ):
                raise MCPServerNameConflict(name)
            server.name = name

        if server_url is not None and server_url != server.server_url:
            new_hash = server_url_hash(server_url)
            existing = await self.server_repo.find_by_url_hash(
                owner_workspace_id=server.owner_workspace_id,
                server_url_hash=new_hash,
            )
            if existing is not None and existing.id != server.id:
                raise MCPServerURLConflict(server_url)
            server.server_url = server_url
            server.server_url_hash = new_hash

        if transport is not None:
            if transport not in _VALID_TRANSPORTS:
                raise ValueError(f"unknown transport: {transport}")
            server.transport = transport

        if credential_plaintext is not None:
            if server.credential_scope != "org":
                raise MCPUserScopeCredentialForbidden(
                    "inline credential update is only valid for credential_scope=org"
                )
            if server.credential_id is None:
                server.credential_id = await self.cred_service.create(
                    kind=CREDENTIAL_KIND_MCP,
                    name=f"mcp:{server.name}:org",
                    plaintext=credential_plaintext,
                )
            else:
                await self.cred_service.update(
                    credential_id=server.credential_id,
                    plaintext=credential_plaintext,
                )

        if headers is not None:
            server.headers = headers
        if timeout is not None:
            server.timeout = timeout
        if sse_read_timeout is not None:
            server.sse_read_timeout = sse_read_timeout

        await self.server_repo.update(server)
        if server_url is not None or transport is not None or credential_plaintext is not None:
            await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    async def delete(self, *, server_id: str) -> None:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)

        for override in await self.override_repo.list_for_server(server_id):
            await self.override_repo.delete(
                workspace_id=override.workspace_id,
                mcp_server_id=server_id,
            )

        for workspace_credential in await self.ws_cred_repo.list_for_server(server_id):
            await self.ws_cred_repo.delete(
                workspace_id=workspace_credential.workspace_id,
                mcp_server_id=server_id,
            )
            with suppress(CredentialNotFound):
                await self.cred_service.delete(credential_id=workspace_credential.credential_id)

        for user_credential in await self.user_cred_repo.list_for_server(server_id):
            await self.user_cred_repo.delete(
                user_id=user_credential.user_id,
                mcp_server_id=server_id,
            )
            with suppress(CredentialNotFound):
                await self.cred_service.delete(credential_id=user_credential.credential_id)

        if server.credential_id is not None:
            with suppress(CredentialNotFound):
                await self.cred_service.delete(credential_id=server.credential_id)

        await self.server_repo.delete(server_id)

    async def refresh_tools(self, *, server_id: str) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    async def test_connection(
        self,
        *,
        server_url: str,
        transport: str,
        auth_method: str,
        credential_scope: str,
        credential_plaintext: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        sse_read_timeout: float = 300.0,
        owner_workspace_id: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        """Dry-run discovery without persisting a server or credentials."""
        self._validate_create_invariants(
            transport=transport,
            auth_method=auth_method,
            credential_scope=credential_scope,
            credential_plaintext=credential_plaintext,
            owner_workspace_id=owner_workspace_id,
        )
        transient = MCPServer(
            org_id=self._ctx.org_id,
            owner_workspace_id=owner_workspace_id,
            name="__test__",
            server_url=server_url,
            server_url_hash=server_url_hash(server_url),
            transport=transport,
            auth_method=auth_method,
            credential_scope=credential_scope,
            credential_id=None,
            headers=headers or {},
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
            created_by_user_id=self._ctx.user.id,
        )

        if credential_scope == "user":
            return True, None, "user-scope: per-user discovery not supported in test-connection"
        token = None if credential_scope == "none" else credential_plaintext
        return await discover_tools(transient, credential_or_token=token)

    async def promote_to_org(
        self,
        *,
        server_id: str,
        share_credential: bool,
    ) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.owner_workspace_id is None:
            raise MCPServerAlreadyOrgWide(server_id)
        if share_credential and server.credential_scope != "workspace":
            raise MCPShareCredentialOnlyForWorkspaceScope()

        original_workspace_id = server.owner_workspace_id

        if server.credential_scope == "workspace" and share_credential:
            workspace_credential = await self.ws_cred_repo.get(
                workspace_id=original_workspace_id,
                mcp_server_id=server_id,
            )
            if workspace_credential is None:
                raise MCPCredentialRequired()
            server.credential_scope = "org"
            server.credential_id = workspace_credential.credential_id
            await self.ws_cred_repo.delete(
                workspace_id=original_workspace_id,
                mcp_server_id=server_id,
            )

        server.owner_workspace_id = None
        await self.server_repo.update(server)

        # New semantics: org installs are invisible by default. Create an
        # enabled override for the source workspace so the promoter still
        # sees the connector immediately after promotion.
        await self.override_repo.upsert(
            workspace_id=original_workspace_id,
            mcp_server_id=server_id,
            enabled=True,
            updated_by_user_id=self._ctx.user.id,
        )

        return await self.server_repo.get(server.id) or server

    async def _effective_credential_mode(
        self,
        *,
        server: MCPServer,
        workspace_id: str,
    ) -> str:
        """Per-workspace effective credential mode.

        A workspace override row may declare ``credential_mode`` (``org`` /
        ``workspace`` / ``user``) that takes precedence over the server-level
        ``credential_scope`` default. Workspace-owned servers have no override
        row and always use ``credential_scope`` directly.
        """
        if server.owner_workspace_id is not None:
            return server.credential_scope
        override = await self.override_repo.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=server.id,
        )
        if override is None or not override.enabled or not override.credential_mode:
            return server.credential_scope
        return override.credential_mode

    async def set_workspace_credential(
        self,
        *,
        server_id: str,
        workspace_id: str,
        plaintext: str,
        credential_name: str | None = None,
    ) -> str:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        effective = await self._effective_credential_mode(server=server, workspace_id=workspace_id)
        if effective != "workspace":
            raise MCPCredentialPathMismatch(
                f"server {server_id} has effective_mode={effective}, not 'workspace'"
            )

        existing = await self.ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if existing is not None:
            await self.cred_service.update(
                credential_id=existing.credential_id,
                plaintext=plaintext,
            )
            return existing.credential_id

        credential_id = await self.cred_service.create(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name or f"mcp:{server.name}:ws:{workspace_id}",
            plaintext=plaintext,
        )
        await self.ws_cred_repo.add(
            WorkspaceMCPCredential(
                org_id=self._ctx.org_id,
                workspace_id=workspace_id,
                mcp_server_id=server_id,
                credential_id=credential_id,
                created_by_user_id=self._ctx.user.id,
            )
        )
        return credential_id

    async def delete_workspace_credential(
        self,
        *,
        server_id: str,
        workspace_id: str,
    ) -> None:
        existing = await self.ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if existing is None:
            return
        await self.ws_cred_repo.delete(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        with suppress(CredentialNotFound):
            await self.cred_service.delete(credential_id=existing.credential_id)

    async def has_workspace_credential(
        self,
        *,
        server_id: str,
        workspace_id: str,
    ) -> bool:
        return (
            await self.ws_cred_repo.get(
                workspace_id=workspace_id,
                mcp_server_id=server_id,
            )
        ) is not None

    async def set_user_credential(
        self,
        *,
        server_id: str,
        user_id: str,
        workspace_id: str,
        plaintext: str,
        credential_name: str | None = None,
    ) -> str:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        effective = await self._effective_credential_mode(server=server, workspace_id=workspace_id)
        if effective != "user":
            raise MCPCredentialPathMismatch(
                f"server {server_id} has effective_mode={effective}, not 'user'"
            )

        existing = await self.user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server_id,
        )
        if existing is not None:
            await self.cred_service.update(
                credential_id=existing.credential_id,
                plaintext=plaintext,
            )
            await self._refresh_tools_for_server_with_token(
                server,
                credential_or_token=plaintext,
            )
            return existing.credential_id

        credential_id = await self.cred_service.create(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name or f"mcp:{server.name}:user:{user_id}",
            plaintext=plaintext,
        )
        await self.user_cred_repo.add(
            UserMCPCredential(
                org_id=self._ctx.org_id,
                user_id=user_id,
                mcp_server_id=server_id,
                credential_id=credential_id,
            )
        )
        await self._refresh_tools_for_server_with_token(
            server,
            credential_or_token=plaintext,
        )
        return credential_id

    async def delete_user_credential(
        self,
        *,
        server_id: str,
        user_id: str,
    ) -> None:
        existing = await self.user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server_id,
        )
        if existing is None:
            return
        await self.user_cred_repo.delete(user_id=user_id, mcp_server_id=server_id)
        with suppress(CredentialNotFound):
            await self.cred_service.delete(credential_id=existing.credential_id)

    async def has_user_credential(
        self,
        *,
        server_id: str,
        user_id: str,
    ) -> bool:
        return (
            await self.user_cred_repo.get(
                user_id=user_id,
                mcp_server_id=server_id,
            )
        ) is not None

    async def set_workspace_override(
        self,
        *,
        server_id: str,
        workspace_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable an org-wide install for a single workspace.

        New semantics: no override row = not visible. ``enabled=True`` makes
        the connector visible to this workspace. ``enabled=False`` (or deleting
        the row) hides it.
        """
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.owner_workspace_id is not None:
            raise MCPWorkspaceOwnedNoOverride()
        if not enabled:
            # Disabling = delete the override row (no row = invisible).
            await self.override_repo.delete(
                workspace_id=workspace_id,
                mcp_server_id=server_id,
            )
            return
        # Enabling = upsert an enabled=True row.
        await self.override_repo.upsert(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
            enabled=True,
            updated_by_user_id=self._ctx.user.id,
        )

    async def _ensure_unique_name_and_url(
        self,
        *,
        name: str,
        server_url: str,
        owner_workspace_id: str | None,
    ) -> None:
        url_hash = server_url_hash(server_url)
        if await self.server_repo.find_by_url_hash(
            owner_workspace_id=owner_workspace_id,
            server_url_hash=url_hash,
        ):
            raise MCPServerURLConflict(server_url)

        existing_servers = await self.server_repo.list_for_org(
            owner_workspace_id=owner_workspace_id
        )
        if any(server.name == name for server in existing_servers):
            raise MCPServerNameConflict(name)

    def _validate_create_invariants(
        self,
        *,
        transport: str,
        auth_method: str,
        credential_scope: str,
        credential_plaintext: str | None,
        owner_workspace_id: str | None,
    ) -> None:
        if auth_method not in _VALID_METHODS:
            raise ValueError(f"unknown auth_method: {auth_method}")
        if credential_scope not in _VALID_SCOPES:
            raise ValueError(f"unknown credential_scope: {credential_scope}")
        if transport not in _VALID_TRANSPORTS:
            raise ValueError(f"unknown transport: {transport}")
        if (auth_method == "none") != (credential_scope == "none"):
            raise ValueError("auth_method=none and credential_scope=none must be set together")
        # Plaintext credential is owned by the static path. OAuth installs get
        # their access/refresh tokens written by the callback handler, not
        # at create time, so they neither require nor accept it. (auth=none
        # forbids it via the (none ↔ none) invariant above.)
        if auth_method == "static":
            if credential_scope in {"org", "workspace"} and not credential_plaintext:
                raise MCPCredentialRequired()
            if credential_scope == "user" and credential_plaintext:
                raise MCPUserScopeCredentialForbidden()
        elif credential_plaintext:
            raise MCPUserScopeCredentialForbidden()
        # OAuth grants are bound to an end-user identity, so the meaningful
        # sharing scopes are "org" (admin's token shared org-wide, per spec
        # §10) and "user" (per-user installs). "workspace" — one shared
        # token for everyone in a workspace — has no real-world OAuth
        # analogue, and the workspace credential row would be created
        # AFTER the server commit (see create()), leaving an orphan
        # unauthed server row. Reject up front.
        if auth_method == "oauth" and credential_scope == "workspace":
            raise ValueError(
                "auth_method=oauth requires credential_scope in {org, user}, not workspace"
            )
        if owner_workspace_id is not None and credential_scope == "org":
            raise ValueError("workspace-private servers cannot use credential_scope=org")

    async def _refresh_tools_for_server(self, server: MCPServer) -> None:
        """Best-effort discovery; failures mark the server unauthenticated."""
        if server.credential_scope == "user":
            return

        # OAuth installs have no credential at create-time — the callback
        # handler writes one and triggers discovery itself. Skip silently.
        if server.auth_method == "oauth" and server.credential_id is None:
            return

        # OAuth tokens have short TTLs (Notion: 1h). Reading the stored
        # access token directly here would let it go stale and surface as
        # a 401 even when a refresh_token is sitting in the vault. Route
        # OAuth through OAuthTokenManager so it auto-refreshes on the
        # admin sync-tools path the same way the agent runtime does.
        if server.auth_method == "oauth" and self._token_manager is not None:
            try:
                refreshed = await self._token_manager.get_valid_access_token(server)
            except OAuthRefreshFailed:
                # Token manager already wrote authed=False + last_error.
                return
            except OAuthRefreshContention:
                # Another worker is mid-refresh; let them finish.
                return
            except OAuthInvalidServerState as exc:
                # Access token expired but no refresh_token metadata to rotate
                # with — e.g. AS never returned a refresh_token on install, or
                # the refresh credential row was purged. Token manager doesn't
                # touch ``authed`` in that case, so we flip it here so the UI
                # surfaces the Re-authenticate button instead of a 500.
                server.authed = False
                server.last_error = f"OAuth re-authentication required: {exc}"[:2048]
                await self.server_repo.update(server)
                return
            await self._refresh_tools_for_server_with_token(server, credential_or_token=refreshed)
            return

        cred_kind = (
            CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
            if server.auth_method == "oauth"
            else CREDENTIAL_KIND_MCP
        )

        token: str | None = None
        if server.credential_scope == "org":
            if server.credential_id is None:
                raise MCPCredentialRequired()
            token = await self.cred_service.get_decrypted(
                credential_id=server.credential_id,
                requesting_kind=cred_kind,
            )
        elif server.credential_scope == "workspace":
            credential_row = await self.ws_cred_repo.get(
                workspace_id=server.owner_workspace_id or "",
                mcp_server_id=server.id,
            )
            if credential_row is None:
                return
            token = await self.cred_service.get_decrypted(
                credential_id=credential_row.credential_id,
                requesting_kind=cred_kind,
            )

        await self._refresh_tools_for_server_with_token(server, credential_or_token=token)

    async def _refresh_tools_for_server_with_token(
        self,
        server: MCPServer,
        *,
        credential_or_token: str | None,
    ) -> None:
        # Delegate to the runtime helper so the OAuth callback handler
        # (which has no MCPServerService) can reuse the same discovery +
        # persistence logic.
        from cubebox.mcp.runtime import refresh_tools_for_server_with_token

        await refresh_tools_for_server_with_token(
            server,
            server_repo=self.server_repo,
            credential_or_token=credential_or_token,
        )
