"""Admin MCP routes: org-wide connector CRUD, overrides, and dry-run checks.

This module hosts both:

* **Legacy** routes under ``/admin/mcp/servers/...`` that operate on
  ``MCPServer`` rows (mounted at startup; will be removed in Task 9 after the
  frontend migrates).
* **Four-layer** routes under ``/admin/mcp/{templates,installs,...}`` introduced
  in Task 4 of the MCP management plan. These operate on
  ``MCPConnectorTemplate`` / ``MCPConnectorInstall`` / ``MCPCredentialGrant``.

Both surfaces share the same ``router`` (and therefore the same ``/admin/mcp``
prefix). The dependencies module exposes the right service per surface.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cubebox.api.schemas.mcp import (
    AdminCreateInstallIn,
    CreateGrantIn,
    CredentialRefOut,
    MCPConnectorInstallListOut,
    MCPConnectorInstallOut,
    MCPConnectorTemplateListOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPOverrideUpdate,
    MCPServerCreateAdmin,
    MCPServerOut,
    MCPServerPatch,
    MCPTestConnectionRequest,
    MCPTestConnectionResponse,
    PatchInstallIn,
    WorkspaceOverrideItem,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user
from cubebox.mcp.dependencies import (
    get_admin_install_service,
    get_admin_mcp_service,
    get_admin_request_context,
    get_audit_sink,
    get_connector_template_service,
)
from cubebox.mcp.exceptions import (
    MCPCredentialRequired,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoOverride,
)
from cubebox.models import MCPConnectorInstall, MCPServer, User
from cubebox.services.mcp import MCPServerService
from cubebox.services.mcp_installs import MCPConnectorInstallService
from cubebox.services.mcp_templates import MCPConnectorTemplateService

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])

# A separate router for the public template list — authenticated but not
# org-admin gated. Mounted at /api/v1 in api/app.py.
public_templates_router = APIRouter(prefix="/mcp", tags=["mcp-public"])


# ---------------- Four-layer helpers ---------------- #


def _template_to_out(
    template: Any,
    *,
    install_summary: dict[str, Any] | None = None,
) -> MCPConnectorTemplateOut:
    return MCPConnectorTemplateOut(
        template_id=template.id,
        slug=template.slug,
        name=template.name,
        provider=template.provider,
        description=template.description,
        server_url=template.server_url,
        transport=template.transport,
        supported_auth_methods=list(template.supported_auth_methods or []),
        default_credential_policy=template.default_credential_policy,
        static_form_schema=template.static_form_schema,
        status=template.status,
        install_summary=install_summary,
    )


def _install_to_out(install: MCPConnectorInstall) -> MCPConnectorInstallOut:
    return MCPConnectorInstallOut(
        install_id=install.id,
        template_id=install.template_id,
        install_scope=install.install_scope,  # type: ignore[arg-type]
        workspace_id=install.workspace_id,
        name=install.name,
        server_url=install.server_url,
        transport=install.transport,
        auth_method=install.auth_method,  # type: ignore[arg-type]
        default_credential_policy=install.default_credential_policy,  # type: ignore[arg-type]
        auth_status=install.auth_status,
        discovery_status=install.discovery_status,
        install_state=install.install_state,
        tool_count=len(install.tools_cache or []),
        last_error=install.last_error,
        auto_enroll_new_workspaces=install.auto_enroll_new_workspaces,
    )


def _policy_field_error(field: str, message: str) -> HTTPException:
    """Match Pydantic's 422 envelope so the API surface is uniform across
    schema-level and service-level rejections of bad policy combos."""
    return HTTPException(
        status_code=422,
        detail=[
            {
                "type": "value_error",
                "loc": ["body", field],
                "msg": message,
                "input": None,
            }
        ],
    )


def _validate_install_policy_pairing(
    *,
    install: MCPConnectorInstall,
    requested_policy: str,
    field: str,
) -> None:
    """Service-level companion to AdminCreateInstallIn._validate_policy_vs_auth.

    Used by PATCH endpoints where the body alone is insufficient (auth_method
    is fixed on the row and not in the request body).
    """
    if requested_policy == "none" and install.auth_method != "none":
        raise _policy_field_error(
            field,
            "credential_policy='none' is only valid when auth_method='none'",
        )
    if requested_policy != "none" and install.auth_method == "none":
        raise _policy_field_error(
            field,
            "auth_method='none' install requires credential_policy='none'",
        )


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
    """Handcrafted org-wide MCP install. **Advanced / debug surface.**

    Prefer ``POST /admin/mcp/catalog/{catalog_id}/install`` for production
    use — it pulls server_url / transport / supported_auth_methods from
    the system catalog and prevents duplicate installs of the same
    connector. This route is retained for cases where an operator needs
    to register a connector that isn't in the catalog yet (M2+ rolls
    out the official catalog UI in Phase 6).
    """
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


# ---------------------------------------------------------------------------
# Four-layer admin routes (templates / installs / grants).
# ---------------------------------------------------------------------------
#
# Coexist with the legacy ``/admin/mcp/servers`` and ``/admin/mcp/catalog``
# surfaces above. Frontend will migrate workspace-by-workspace; legacy mount
# stays in ``api/app.py`` until Task 9 of the four-layer plan.


@router.get("/templates", response_model=MCPConnectorTemplateListOut)
async def list_admin_templates(
    svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorTemplateListOut:
    """Admin view over the global connector template catalog."""
    templates = await svc.list_active()
    return MCPConnectorTemplateListOut(items=[_template_to_out(t) for t in templates])


@router.get("/installs", response_model=MCPConnectorInstallListOut)
async def list_admin_installs(
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
) -> MCPConnectorInstallListOut:
    """List every org-scope install in the admin's current org."""
    org_rows = await svc._install_repo.list_org_installs()
    return MCPConnectorInstallListOut(
        items=[_install_to_out(install) for install in org_rows],
    )


@router.post(
    "/installs",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConnectorInstallOut,
)
async def create_admin_install(
    body: AdminCreateInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    template_svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    """Create an org-scope install, optionally fanning out into workspaces."""
    if body.template_id is None:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "template_id"],
                    "msg": "template_id is required for org installs in Task 4",
                    "input": None,
                }
            ],
        )

    try:
        template = await template_svc.get_active(body.template_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "connector_template_not_found"},
        ) from exc

    try:
        install = await svc.create_from_template_for_org(
            template=template,
            auth_method=body.auth_method,
            credential_policy=body.default_credential_policy,
            distribution=body.auto_enable.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_distribution", "msg": str(exc)}) from exc

    await audit.record(
        event="mcp.install.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install.id,
        details={"scope": "org", "template_id": template.id},
    )
    return _install_to_out(install)


@router.get(
    "/installs/{install_id}",
    response_model=MCPConnectorInstallOut,
)
async def get_admin_install(
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
) -> MCPConnectorInstallOut:
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    return _install_to_out(install)


@router.patch(
    "/installs/{install_id}",
    response_model=MCPConnectorInstallOut,
)
async def patch_admin_install(
    install_id: str,
    body: PatchInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})

    if body.default_credential_policy is not None:
        _validate_install_policy_pairing(
            install=install,
            requested_policy=body.default_credential_policy,
            field="default_credential_policy",
        )
        install.default_credential_policy = body.default_credential_policy

    if body.auto_enroll_new_workspaces is not None:
        install.auto_enroll_new_workspaces = body.auto_enroll_new_workspaces
    if body.headers is not None:
        install.headers = body.headers
    if body.name is not None:
        install.name = body.name
    if body.server_url is not None and body.server_url != install.server_url:
        # ``server_url_hash`` is the indexed half of the partial unique
        # constraints on org-scope / workspace-scope installs. If we
        # update ``server_url`` without recomputing the hash, the row
        # would survive into a state where two installs with different
        # URLs could share the same hash (and conversely, the same URL
        # could appear twice with different hashes), breaking the
        # uniqueness guarantee the indexes are supposed to provide.
        # Derive the hash here from the new URL — any client-supplied
        # hash on the body is ignored on purpose so a tampered or
        # half-formed patch can't desync the two fields.
        from cubebox.mcp._constants import server_url_hash

        install.server_url = body.server_url
        install.server_url_hash = server_url_hash(body.server_url)
    if body.transport is not None:
        install.transport = body.transport

    saved = await svc._install_repo.update(install)
    await audit.record(
        event="mcp.install.patched",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
    )
    return _install_to_out(saved)


@router.delete(
    "/installs/{install_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_admin_install(
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    try:
        await svc.uninstall(install_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"}) from exc
    await audit.record(
        event="mcp.install.uninstalled",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
    )


@router.post(
    "/installs/{install_id}/grants/org",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_org_grant(
    install_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPCredentialGrantStatusOut:
    """Create an org-scope grant for an install (static auth only)."""
    if body.credential_plaintext is None:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "credential_plaintext"],
                    "msg": "credential_plaintext required for static org grants",
                    "input": None,
                }
            ],
        )
    try:
        grant = await svc.create_static_grant(
            install_id=install_id,
            grant_scope="org",
            plaintext=body.credential_plaintext,
            name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_grant", "msg": str(exc)}) from exc
    await audit.record(
        event="mcp.grant.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "org"},
    )
    return MCPCredentialGrantStatusOut(
        install_id=install_id,
        grant_scope="org",
        workspace_id=None,
        user_id=None,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{install_id}/grants/org",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_admin_org_grant(
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(install_id=install_id, grant_scope="org")
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "org"},
    )


@router.post(
    "/installs/{install_id}/grants/org/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def admin_org_grant_oauth_start(
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — present for OpenAPI clarity
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces an org-scope grant.

    Task 4 registers the contract; the AS-discovery + DCR + PKCE + state-token
    issuance for four-layer grant flows is wired in plan §Task 6. The legacy
    ``/admin/mcp/installs/{id}/oauth/start`` route remains active for
    ``MCPServer``-based installs until Task 9.
    """
    raise HTTPException(
        status_code=501,
        detail={
            "code": "mcp_oauth.four_layer_start_not_yet_wired",
            "message": (
                "Four-layer OAuth start is registered but the AS handshake"
                " wiring lands in plan Task 6."
            ),
            "install_id": install_id,
        },
    )


# ---------------------------------------------------------------------------
# Public template list (authenticated, not org-admin gated).
# ---------------------------------------------------------------------------


@public_templates_router.get(
    "/templates",
    response_model=MCPConnectorTemplateListOut,
)
async def list_public_templates(
    svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    _user: Annotated[User, Depends(current_active_user)],
) -> MCPConnectorTemplateListOut:
    """Public template list — authenticated, not org-scoped."""
    templates = await svc.list_active()
    return MCPConnectorTemplateListOut(items=[_template_to_out(t) for t in templates])
