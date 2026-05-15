"""MCP server discovery for the cubepi runtime (M2.4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models import MCPServer
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)

MCPTransport = Literal["sse", "streamable_http"]
_VALID_TRANSPORTS: frozenset[str] = frozenset({"sse", "streamable_http"})

logger = logging.getLogger(__name__)


@dataclass
class CubepiMCPServerSpec:
    """Resolved MCP server ready for cubepi.mcp.load_mcp_tools_http."""

    server_id: str
    server_name: str
    url: str
    transport: MCPTransport
    headers: dict[str, str] = field(default_factory=dict)
    tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)


async def discover_workspace_mcp_servers_for_cubepi(
    *,
    session: AsyncSession,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
) -> list[CubepiMCPServerSpec]:
    """Resolve workspace-enabled MCP servers + decrypt credentials → CubepiMCPServerSpec list.

    Re-uses the existing DB resolution logic (MCPServerRepository.list_for_workspace) and
    credential decryption from CredentialService. Servers with missing or unresolvable
    credentials are skipped with a warning rather than raising.

    Only HTTP/SSE transports are supported (stdio was removed; this mirrors cubebox's current
    production stance). Servers requiring a user credential (scope='user') without a resolved
    token are excluded.
    """
    server_repo = MCPServerRepository(session, org_id=org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=org_id)
    override_repo = WorkspaceMCPOverrideRepository(session, org_id=org_id)

    specs: list[CubepiMCPServerSpec] = []
    for server in await server_repo.list_for_workspace(workspace_id):
        if server.transport not in _VALID_TRANSPORTS:
            logger.warning(
                "MCP server '%s' has unsupported transport %r; skipping",
                server.name,
                server.transport,
            )
            continue
        try:
            # Resolve the per-workspace effective credential mode first.  A
            # workspace override may flip the server's default credential_scope
            # (e.g. shared org server promoted to per-user, or any server
            # downgraded to passthrough).
            effective_scope = await _effective_credential_mode(
                server=server,
                workspace_id=workspace_id,
                override_repo=override_repo,
            )
            token = await _resolve_token_for_cubepi(
                server_id=server.id,
                server_name=server.name,
                server_org_id=server.org_id,
                auth_method=server.auth_method,
                effective_scope=effective_scope,
                credential_id=server.credential_id,
                workspace_id=workspace_id,
                user_id=user_id,
                cred_service=cred_service,
                ws_cred_repo=ws_cred_repo,
                user_cred_repo=user_cred_repo,
                signer=signer,
            )
        except CredentialNotFound:
            logger.warning(
                "MCP server '%s' references a missing credential; skipping",
                server.name,
            )
            continue
        except Exception as exc:
            logger.warning(
                "MCP server '%s' credential resolution failed: %s; skipping",
                server.name,
                exc,
            )
            continue

        # Servers that require a credential but have none resolved are excluded.
        if token is None and effective_scope != "none":
            continue

        # Build the Authorization header if we have a token.
        headers: dict[str, str] = dict(server.headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"

        specs.append(
            CubepiMCPServerSpec(
                server_id=server.id,
                server_name=server.name,
                url=server.server_url,
                transport=cast(MCPTransport, server.transport),
                headers=headers,
                tool_citations=dict(server.tool_citations or {}),
            )
        )

    return specs


async def _effective_credential_mode(
    *,
    server: MCPServer,
    workspace_id: str,
    override_repo: WorkspaceMCPOverrideRepository,
) -> str:
    """Per-workspace effective credential mode for an MCP server.

    A workspace override may declare ``credential_mode`` that takes precedence
    over the server-level ``credential_scope`` default. Workspace-owned servers
    always fall back to their declared scope (no overrides apply).
    """
    if server.owner_workspace_id is not None:
        return server.credential_scope
    override = await override_repo.get_for_workspace_and_server(
        workspace_id=workspace_id,
        mcp_server_id=server.id,
    )
    if override is None or not override.enabled or not override.credential_mode:
        return server.credential_scope
    return override.credential_mode


async def _resolve_token_for_cubepi(
    *,
    server_id: str,
    server_name: str,
    server_org_id: str,
    auth_method: str,
    effective_scope: str,
    credential_id: str | None,
    workspace_id: str,
    user_id: str,
    cred_service: CredentialService,
    ws_cred_repo: WorkspaceMCPCredentialRepository,
    user_cred_repo: UserMCPCredentialRepository,
    signer: MCPUserTokenSigner,
) -> str | None:
    """Resolve the bearer token/credential for one MCP server.

    Mirrors the logic in runtime._resolve_token. ``effective_scope`` is the
    workspace-resolved credential mode (see ``_effective_credential_mode``)
    so that ``WorkspaceMCPOverride.credential_mode`` is honored. For
    passthrough servers (``effective_scope == "none"``) a short-lived cubebox
    identity token is signed so the MCP server can enforce tenant scoping.
    """
    cred_kind = (
        CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN if auth_method == "oauth" else CREDENTIAL_KIND_MCP
    )

    if effective_scope == "org":
        if credential_id is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=credential_id,
            requesting_kind=cred_kind,
        )

    if effective_scope == "workspace":
        workspace_credential = await ws_cred_repo.get(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if workspace_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=workspace_credential.credential_id,
            requesting_kind=cred_kind,
        )

    if effective_scope == "user":
        user_credential = await user_cred_repo.get(
            user_id=user_id,
            mcp_server_id=server_id,
        )
        if user_credential is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=user_credential.credential_id,
            requesting_kind=cred_kind,
        )

    if effective_scope == "none":
        # Passthrough: sign a short-lived cubebox identity token so the MCP
        # server can enforce tenant scoping even without a user credential.
        return await signer.sign(
            user_id=user_id,
            org_id=server_org_id,
            workspace_id=workspace_id,
            mcp_server_id=server_id,
            ttl=_USER_TOKEN_TTL,
        )

    return None
