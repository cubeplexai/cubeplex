"""Admin MCP routes: org-wide connector CRUD, overrides, and dry-run checks."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cubebox.api.schemas.mcp import (
    CredentialRefOut,
    MCPOverrideUpdate,
    MCPServerCreateAdmin,
    MCPServerOut,
    MCPServerPatch,
    MCPTestConnectionRequest,
    MCPTestConnectionResponse,
    WorkspaceOverrideItem,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.mcp.dependencies import (
    get_admin_mcp_service,
    get_admin_request_context,
    get_audit_sink,
)
from cubebox.mcp.exceptions import (
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoOverride,
)
from cubebox.models import MCPServer
from cubebox.services.mcp import MCPServerService

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])


def _server_to_out(
    server: MCPServer,
    *,
    include_tools_cache: bool,
    cred_name: str | None = None,
) -> MCPServerOut:
    credential: CredentialRefOut | None = None
    if server.credential_id is not None:
        credential = CredentialRefOut(
            id=server.credential_id,
            name=cred_name or "credential",
            has_value=True,
        )
    return MCPServerOut(
        id=server.id,
        name=server.name,
        server_url=server.server_url,
        transport=server.transport,
        auth_method=server.auth_method,
        credential_scope=server.credential_scope,
        credential=credential,
        owner_workspace_id=server.owner_workspace_id,
        headers=server.headers or {},
        tools_cache=server.tools_cache if include_tools_cache else None,
        authed=server.authed,
        last_error=server.last_error,
        last_discovered_at=server.last_discovered_at,
        timeout=server.timeout,
        sse_read_timeout=server.sse_read_timeout,
        created_by_user_id=server.created_by_user_id,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.get("/servers")
async def list_servers(
    scope: str | None = Query(default=None),
    owner_workspace_id: str | None = Query(default=None),
    has_error: bool | None = Query(default=None),
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> list[MCPServerOut]:
    servers = await svc.server_repo.list_for_org()
    if scope is not None:
        servers = [server for server in servers if server.credential_scope == scope]
    if owner_workspace_id is not None:
        servers = [server for server in servers if server.owner_workspace_id == owner_workspace_id]
    if has_error is True:
        servers = [server for server in servers if not server.authed]
    return [_server_to_out(server, include_tools_cache=False) for server in servers]


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: MCPServerCreateAdmin,
    svc: MCPServerService = Depends(get_admin_mcp_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    try:
        server = await svc.create(
            name=body.name,
            server_url=body.server_url,
            transport=body.transport,
            auth_method=body.auth_method,
            credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
            owner_workspace_id=None,
            headers=body.headers,
            timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPOAuthNotImplemented as exc:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"}) from exc
    except MCPServerURLConflict as exc:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"}) from exc
    except MCPServerNameConflict as exc:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"}) from exc
    except MCPCredentialRequired as exc:
        raise HTTPException(400, detail={"code": "mcp_credential_required"}) from exc
    except MCPUserScopeCredentialForbidden as exc:
        raise HTTPException(
            400,
            detail={"code": "mcp_user_scope_credential_forbidden"},
        ) from exc

    await audit.record(
        event="mcp.server.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server.id,
        details={"scope": server.credential_scope},
    )
    return _server_to_out(server, include_tools_cache=True)


@router.get("/servers/{server_id}")
async def get_server(
    server_id: str,
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    return _server_to_out(server, include_tools_cache=True)


@router.patch("/servers/{server_id}")
async def patch_server(
    server_id: str,
    body: MCPServerPatch,
    svc: MCPServerService = Depends(get_admin_mcp_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    try:
        server = await svc.update(
            server_id=server_id,
            name=body.name,
            server_url=body.server_url,
            transport=body.transport,
            credential_plaintext=body.credential_plaintext,
            headers=body.headers,
            timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    except MCPServerNameConflict as exc:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"}) from exc
    except MCPServerURLConflict as exc:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"}) from exc
    except MCPUserScopeCredentialForbidden as exc:
        raise HTTPException(
            400,
            detail={"code": "mcp_user_scope_credential_forbidden"},
        ) from exc

    await audit.record(
        event="mcp.server.updated",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
    )
    return _server_to_out(server, include_tools_cache=True)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    svc: MCPServerService = Depends(get_admin_mcp_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> None:
    try:
        await svc.delete(server_id=server_id)
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    await audit.record(
        event="mcp.server.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
    )


@router.post("/servers/{server_id}/refresh-tools")
async def refresh_tools(
    server_id: str,
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> MCPServerOut:
    try:
        server = await svc.refresh_tools(server_id=server_id)
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    return _server_to_out(server, include_tools_cache=True)


@router.post("/test-connection")
async def test_connection(
    body: MCPTestConnectionRequest,
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> MCPTestConnectionResponse:
    try:
        success, tools, error = await svc.test_connection(
            server_url=body.server_url,
            transport=body.transport,
            auth_method=body.auth_method,
            credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            headers=body.headers,
            timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPOAuthNotImplemented as exc:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"}) from exc
    except MCPCredentialRequired as exc:
        raise HTTPException(400, detail={"code": "mcp_credential_required"}) from exc
    except MCPUserScopeCredentialForbidden as exc:
        raise HTTPException(
            400,
            detail={"code": "mcp_user_scope_credential_forbidden"},
        ) from exc
    return MCPTestConnectionResponse(success=success, tools=tools, error=error)


@router.get("/servers/{server_id}/overrides")
async def get_overrides(
    server_id: str,
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> list[WorkspaceOverrideItem]:
    """List workspaces that have explicitly disabled this org-wide install."""
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id is not None:
        raise HTTPException(400, detail={"code": "mcp_workspace_owned_no_override"})
    overrides = await svc.override_repo.list_for_server(server_id)
    return [
        WorkspaceOverrideItem(workspace_id=override.workspace_id, enabled=override.enabled)
        for override in overrides
    ]


@router.put("/servers/{server_id}/overrides")
async def put_override(
    server_id: str,
    body: MCPOverrideUpdate,
    svc: MCPServerService = Depends(get_admin_mcp_service),
) -> list[WorkspaceOverrideItem]:
    """Disable or re-enable an org-wide install for one workspace."""
    try:
        await svc.set_workspace_override(
            server_id=server_id,
            workspace_id=body.workspace_id,
            enabled=body.enabled,
        )
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    except MCPWorkspaceOwnedNoOverride as exc:
        raise HTTPException(
            400,
            detail={"code": "mcp_workspace_owned_no_override"},
        ) from exc

    overrides = await svc.override_repo.list_for_server(server_id)
    return [
        WorkspaceOverrideItem(workspace_id=override.workspace_id, enabled=override.enabled)
        for override in overrides
    ]
