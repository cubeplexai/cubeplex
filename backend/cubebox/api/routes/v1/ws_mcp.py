"""Workspace MCP routes: member-managed private connectors and credentials.

Hosts both:

* **Legacy** routes under ``/ws/{ws}/mcp/servers/...`` operating on
  ``MCPServer`` rows.
* **Four-layer** routes added in Task 4 of the MCP management plan
  (``/templates``, ``/connectors``, ``/installs``, ``/installs/{id}/grants/...``).

Both surfaces share the same router (``/ws/{workspace_id}/mcp`` prefix). The
legacy mount stays until Task 9.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from cubebox.api.routes.v1.admin_mcp import (
    _install_to_out,
    _server_to_out,
    _template_to_out,
    _validate_install_policy_pairing,
)
from cubebox.api.schemas.mcp import (
    CreateGrantIn,
    MCPConnectorInstallOut,
    MCPConnectorTemplateListOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPCredentialStatus,
    MCPCredentialUpsert,
    MCPEffectiveConnectorListOut,
    MCPEffectiveConnectorOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPPromoteRequest,
    MCPServerCreateWS,
    MCPServerListWS,
    MCPServerOut,
    MCPServerPatch,
    MCPTestConnectionRequest,
    MCPTestConnectionResponse,
    MCPWorkspaceConnectorStateOut,
    PatchWorkspaceStateIn,
    WorkspaceCreateInstallIn,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_admin, require_member
from cubebox.mcp.dependencies import (
    get_audit_sink,
    get_connector_template_service,
    get_mcp_service,
    get_ws_effective_service,
    get_ws_install_service,
)
from cubebox.mcp.effective import (
    MCPEffectiveConnectorDTO,
    MCPEffectiveConnectorService,
)
from cubebox.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
)
from cubebox.middleware.citations.config import CitationConfig
from cubebox.models import MCPServer
from cubebox.services.mcp import MCPServerService
from cubebox.services.mcp_installs import MCPConnectorInstallService
from cubebox.services.mcp_templates import MCPConnectorTemplateService


class ToolCitationsResponse(BaseModel):
    server_id: str
    server_name: str
    tools_cache: list[dict[str, Any]]
    tool_citations: dict[str, dict[str, Any]]
    catalog_defaults: dict[str, dict[str, Any]] | None
    orphan_keys: list[str]


class ToolCitationsPatch(BaseModel):
    tool_citations: dict[str, dict[str, Any]]


router = APIRouter(prefix="/ws/{workspace_id}/mcp", tags=["workspace-mcp"])


async def _get_workspace_owned_server(
    *,
    svc: MCPServerService,
    server_id: str,
    workspace_id: str,
) -> MCPServer:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id != workspace_id:
        raise HTTPException(403, detail={"code": "mcp_server_not_owned_by_workspace"})
    return server


async def _get_workspace_visible_server(
    *,
    svc: MCPServerService,
    server_id: str,
    workspace_id: str,
) -> MCPServer:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id == workspace_id:
        return server
    if server.owner_workspace_id is None:
        # New semantics: visible only if an enabled=True override row exists.
        override = await svc.override_repo.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if override is not None and override.enabled:
            return server
    raise HTTPException(403, detail={"code": "mcp_server_not_available_to_workspace"})


def _map_create_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MCPServerURLConflict):
        return HTTPException(409, detail={"code": "mcp_server_url_conflict"})
    if isinstance(exc, MCPServerNameConflict):
        return HTTPException(409, detail={"code": "mcp_server_name_conflict"})
    if isinstance(exc, MCPCredentialRequired):
        return HTTPException(400, detail={"code": "mcp_credential_required"})
    if isinstance(exc, MCPUserScopeCredentialForbidden):
        return HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"})
    if isinstance(exc, ValueError):
        return HTTPException(400, detail={"code": "mcp_invalid_request"})
    return HTTPException(500, detail={"code": "mcp_internal_error"})


@router.get("/servers")
async def list_servers(
    workspace_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerListWS:
    """Return workspace-private installs and inherited org-wide installs.

    Inherited installs include every org-wide row that hasn't been explicitly
    disabled for this workspace via ``workspace_mcp_overrides``.
    """
    owned = await svc.server_repo.list_for_org(owner_workspace_id=workspace_id)
    paired = await svc.server_repo.list_org_wide_with_workspace_override(workspace_id)
    inherited: list[MCPServer] = [
        srv for srv, override in paired if override is not None and override.enabled
    ]

    return MCPServerListWS(
        owned=[_server_to_out(server, include_tools_cache=False) for server in owned],
        inherited=[_server_to_out(server, include_tools_cache=False) for server in inherited],
    )


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    workspace_id: str,
    body: MCPServerCreateWS,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
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
            owner_workspace_id=workspace_id,
            headers=body.headers,
            timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except (
        MCPServerURLConflict,
        MCPServerNameConflict,
        MCPCredentialRequired,
        MCPUserScopeCredentialForbidden,
        ValueError,
    ) as exc:
        raise _map_create_error(exc) from exc

    await audit.record(
        event="mcp.server.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server.id,
        details={"workspace_id": workspace_id, "scope": server.credential_scope},
    )
    return _server_to_out(server, include_tools_cache=True)


@router.get("/servers/{server_id}")
async def get_server(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    server = await _get_workspace_visible_server(
        svc=svc,
        server_id=server_id,
        workspace_id=workspace_id,
    )
    return _server_to_out(server, include_tools_cache=True)


@router.patch("/servers/{server_id}")
async def patch_server(
    workspace_id: str,
    server_id: str,
    body: MCPServerPatch,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    await _get_workspace_owned_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
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
        raise HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"}) from exc

    await audit.record(
        event="mcp.server.updated",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
        details={"workspace_id": workspace_id},
    )
    return _server_to_out(server, include_tools_cache=True)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
    audit: AuditSink = Depends(get_audit_sink),
) -> None:
    await _get_workspace_owned_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    try:
        await svc.delete(server_id=server_id)
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    await audit.record(
        event="mcp.server.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
        details={"workspace_id": workspace_id},
    )


@router.post("/servers/{server_id}/refresh-tools")
async def refresh_tools(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    await _get_workspace_owned_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    try:
        server = await svc.refresh_tools(server_id=server_id)
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    return _server_to_out(server, include_tools_cache=True)


@router.post("/test-connection")
async def test_connection(
    workspace_id: str,
    body: MCPTestConnectionRequest,
    svc: MCPServerService = Depends(get_mcp_service),
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
            owner_workspace_id=workspace_id,
        )
    except (
        MCPCredentialRequired,
        MCPUserScopeCredentialForbidden,
        ValueError,
    ) as exc:
        raise _map_create_error(exc) from exc
    return MCPTestConnectionResponse(success=success, tools=tools, error=error)


@router.post("/servers/{server_id}/promote-to-org")
async def promote_to_org(
    workspace_id: str,
    server_id: str,
    body: MCPPromoteRequest,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    await _get_workspace_owned_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    try:
        server = await svc.promote_to_org(
            server_id=server_id,
            share_credential=body.share_credential,
        )
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    except MCPServerAlreadyOrgWide as exc:
        raise HTTPException(409, detail={"code": "mcp_server_already_org_wide"}) from exc
    except MCPShareCredentialOnlyForWorkspaceScope as exc:
        raise HTTPException(
            400,
            detail={"code": "mcp_share_credential_only_for_workspace_scope"},
        ) from exc
    except MCPCredentialRequired as exc:
        raise HTTPException(400, detail={"code": "mcp_credential_required"}) from exc

    await audit.record(
        event="mcp.server.promoted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
        details={"workspace_id": workspace_id, "share_credential": body.share_credential},
    )
    return _server_to_out(server, include_tools_cache=True)


@router.get("/servers/{server_id}/workspace-credential")
async def get_workspace_credential_status(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPCredentialStatus:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    return MCPCredentialStatus(
        has_value=await svc.has_workspace_credential(
            server_id=server_id,
            workspace_id=workspace_id,
        )
    )


@router.put("/servers/{server_id}/workspace-credential")
async def put_workspace_credential(
    workspace_id: str,
    server_id: str,
    body: MCPCredentialUpsert,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPCredentialStatus:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    try:
        await svc.set_workspace_credential(
            server_id=server_id,
            workspace_id=workspace_id,
            plaintext=body.plaintext,
            credential_name=body.name,
        )
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    except MCPCredentialPathMismatch as exc:
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"}) from exc
    return MCPCredentialStatus(has_value=True)


@router.delete(
    "/servers/{server_id}/workspace-credential",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_credential(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> None:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    await svc.delete_workspace_credential(server_id=server_id, workspace_id=workspace_id)


@router.get("/servers/{server_id}/my-credential")
async def get_my_credential_status(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPCredentialStatus:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    return MCPCredentialStatus(
        has_value=await svc.has_user_credential(server_id=server_id, user_id=ctx.user.id)
    )


@router.put("/servers/{server_id}/my-credential")
async def put_my_credential(
    workspace_id: str,
    server_id: str,
    body: MCPCredentialUpsert,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPCredentialStatus:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    try:
        await svc.set_user_credential(
            server_id=server_id,
            user_id=ctx.user.id,
            workspace_id=workspace_id,
            plaintext=body.plaintext,
            credential_name=body.name,
        )
    except MCPServerNotFound as exc:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"}) from exc
    except MCPCredentialPathMismatch as exc:
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"}) from exc
    return MCPCredentialStatus(has_value=True)


@router.delete("/servers/{server_id}/my-credential", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_credential(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
) -> None:
    await _get_workspace_visible_server(svc=svc, server_id=server_id, workspace_id=workspace_id)
    await svc.delete_user_credential(server_id=server_id, user_id=ctx.user.id)


async def _build_catalog_defaults(
    server: MCPServer,
    session: Any,
) -> dict[str, dict[str, Any]] | None:
    """Return the catalog connector's tool_citations, or None if server is not catalog-backed."""
    if server.catalog_connector_id is None:
        return None
    from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

    catalog_repo = MCPCatalogConnectorRepository(session)
    catalog = await catalog_repo.get_by_id(server.catalog_connector_id)
    if catalog is None:
        return None
    return dict(catalog.tool_citations or {})


async def _get_server_for_citation_admin(
    *,
    svc: MCPServerService,
    server_id: str,
    workspace_id: str,
    actor_user_id: str,
    actor_org_id: str,
) -> MCPServer:
    """Resolve a server for citation read/write with the citation-specific access policy.

    - 404 if no such server exists in the actor's org.
    - Workspace-owned by ``workspace_id`` → allowed (workspace admin dependency enforced
      at the route level).
    - Org-wide (owner_workspace_id is None) → org-admin role required.
    - Owned by a different workspace → 403.

    This helper is intentionally broader than ``_get_workspace_visible_server``: it
    does NOT require an enabled override row, so org-admins can manage citations on
    org-wide installs that haven't yet been enabled for any workspace.
    """
    from cubebox.repositories.organization_membership import OrganizationMembershipRepository

    server = await svc.server_repo.get(server_id)
    if server is None or server.org_id != actor_org_id:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})

    if server.owner_workspace_id == workspace_id:
        return server

    if server.owner_workspace_id is None:
        is_org_admin = await OrganizationMembershipRepository(svc.server_repo.session).is_admin(
            user_id=actor_user_id, org_id=actor_org_id
        )
        if not is_org_admin:
            raise HTTPException(
                status_code=403,
                detail={"code": "mcp_org_wide_citations_require_org_admin"},
            )
        return server

    raise HTTPException(403, detail={"code": "mcp_server_not_owned_by_workspace"})


@router.get("/servers/{server_id}/tool-citations")
async def get_tool_citations(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
) -> ToolCitationsResponse:
    """Return the current tool-citations config for a server, plus catalogue defaults.

    Fast path: server is visible via the workspace (ws-owned or org-wide with an
    enabled override). Fallback: org-admins can also view org-wide installs that
    have no enabled override yet (admin-panel citation tab scenario).
    """
    try:
        server = await _get_workspace_visible_server(
            svc=svc,
            server_id=server_id,
            workspace_id=workspace_id,
        )
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        # Fallback: org-admins may view org-wide installs without an enabled override.
        server = await _get_server_for_citation_admin(
            svc=svc,
            server_id=server_id,
            workspace_id=workspace_id,
            actor_user_id=ctx.user.id,
            actor_org_id=ctx.org_id,
        )

    tools_cache = list(server.tools_cache or [])
    known_names = {t["name"] for t in tools_cache}
    citations = dict(server.tool_citations or {})
    orphan_keys = sorted(k for k in citations if k not in known_names)
    catalog_defaults = await _build_catalog_defaults(server, svc.server_repo.session)
    return ToolCitationsResponse(
        server_id=server.id,
        server_name=server.name,
        tools_cache=tools_cache,
        tool_citations=citations,
        catalog_defaults=catalog_defaults,
        orphan_keys=orphan_keys,
    )


@router.patch("/servers/{server_id}/tool-citations")
async def patch_tool_citations(
    workspace_id: str,
    server_id: str,
    body: ToolCitationsPatch,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_admin),
    audit: AuditSink = Depends(get_audit_sink),
) -> ToolCitationsResponse:
    """Replace the tool-citations config for a server (admin only).

    Each key must match a tool name present in tools_cache, and each value must
    be a valid CitationConfig. Returns 422 if either invariant is violated.

    For org-wide servers (owner_workspace_id is None), org-admin role is required —
    otherwise a workspace admin in one workspace could mutate shared state that
    affects other workspaces with the override enabled.  The server does NOT need
    to have an enabled workspace override to be editable by an org-admin (admin
    panel scenario where the server was installed org-wide but not yet enabled).
    """
    server = await _get_server_for_citation_admin(
        svc=svc,
        server_id=server_id,
        workspace_id=workspace_id,
        actor_user_id=ctx.user.id,
        actor_org_id=ctx.org_id,
    )

    known_names = {t["name"] for t in (server.tools_cache or [])}

    errors: list[dict[str, Any]] = []
    parsed: dict[str, dict[str, Any]] = {}
    for tool_name, raw in body.tool_citations.items():
        if tool_name not in known_names:
            errors.append({"tool": tool_name, "msg": "tool not in tools_cache"})
            continue
        try:
            CitationConfig(**raw)
        except ValidationError as exc:
            errors.append({"tool": tool_name, "msg": str(exc)})
            continue
        parsed[tool_name] = raw

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    server.tool_citations = parsed
    await svc.server_repo.update(server)

    await audit.record(
        event="mcp.tool_citations.patch",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=server_id,
        details={"workspace_id": workspace_id},
    )

    catalog_defaults = await _build_catalog_defaults(server, svc.server_repo.session)
    return ToolCitationsResponse(
        server_id=server.id,
        server_name=server.name,
        tools_cache=list(server.tools_cache or []),
        tool_citations=dict(server.tool_citations or {}),
        catalog_defaults=catalog_defaults,
        orphan_keys=[],
    )


# ---------------------------------------------------------------------------
# Four-layer workspace routes (templates / connectors / installs / grants).
# ---------------------------------------------------------------------------
#
# Coexist with the legacy ``/ws/{ws}/mcp/servers/...`` routes above; the
# legacy mount stays until Task 9 of the MCP management plan.
#
# Authorization recap (per spec §User Roles And Permissions):
# - All routes require workspace membership (``require_member``).
# - ``POST /installs``, ``DELETE /installs/{id}``, ``PATCH /connectors/{id}/state``,
#   and the workspace-scope grant routes require workspace admin
#   (``require_admin``).
# - ``*/grants/me*`` routes are open to any member.


def _dto_to_effective_out(dto: MCPEffectiveConnectorDTO) -> MCPEffectiveConnectorOut:
    template_out: MCPConnectorTemplateOut | None = None
    if dto.template is not None:
        template_out = _template_to_out(dto.template)
    state_out: MCPWorkspaceConnectorStateOut | None = None
    if dto.workspace_state is not None:
        state_out = MCPWorkspaceConnectorStateOut(
            workspace_id=dto.workspace_state.workspace_id,
            install_id=dto.workspace_state.install_id,
            enabled=dto.workspace_state.enabled,
            credential_policy=dto.workspace_state.credential_policy,  # type: ignore[arg-type]
            enablement_source=dto.workspace_state.enablement_source,
        )
    return MCPEffectiveConnectorOut(
        template=template_out,
        install=_install_to_out(dto.install),
        workspace_state=state_out,
        credential_policy=dto.credential_policy,
        required_grant_scope=dto.required_grant_scope,
        credential_availability=dto.credential_availability,
        credential_source=(dto.credential_source if dto.credential_source != "none" else None),
        usable=dto.usable,
        reason=dto.reason,
    )


@router.get("/templates", response_model=MCPConnectorTemplateListOut)
async def list_workspace_templates(
    workspace_id: str,  # noqa: ARG001 — path param, future workspace-scoped filtering
    svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    _ctx: Annotated[RequestContext, Depends(require_member)],
) -> MCPConnectorTemplateListOut:
    """Workspace view over the global connector template catalog."""
    templates = await svc.list_active()
    return MCPConnectorTemplateListOut(items=[_template_to_out(t) for t in templates])


@router.get("/connectors", response_model=MCPEffectiveConnectorListOut)
async def list_workspace_connectors(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
) -> MCPEffectiveConnectorListOut:
    """Effective connector list for the workspace + current user."""
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id,
        ctx.user.id,
        include_unusable=True,
    )
    return MCPEffectiveConnectorListOut(
        items=[_dto_to_effective_out(dto) for dto in dtos],
    )


@router.post(
    "/installs",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConnectorInstallOut,
)
async def create_workspace_install(
    workspace_id: str,
    body: WorkspaceCreateInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    template_svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    """Workspace-local install creation. Admin-only.

    Uses :class:`WorkspaceCreateInstallIn` — ``install_scope`` is pinned
    to ``"workspace"`` at the schema layer so attempts to POST an
    ``install_scope: "org"`` body to this route are rejected with 422
    before reaching the handler. The org-scope path lives under
    ``POST /api/v1/admin/mcp/installs``.
    """
    if body.template_id is None:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "template_id"],
                    "msg": "template_id is required for workspace installs in Task 4",
                    "input": None,
                }
            ],
        )
    try:
        template = await template_svc.get_active(body.template_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "connector_template_not_found"}) from exc

    install = await svc.create_from_template_for_workspace(
        template=template,
        workspace_id=workspace_id,
        auth_method=body.auth_method,
        credential_policy=body.default_credential_policy,
    )
    await audit.record(
        event="mcp.install.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install.id,
        details={"scope": "workspace", "workspace_id": workspace_id},
    )
    return _install_to_out(install)


@router.delete(
    "/installs/{install_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_install(
    workspace_id: str,
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    install = await svc._install_repo.get(install_id)
    if install is None or install.workspace_id != workspace_id:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    try:
        await svc.uninstall(install_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"}) from exc
    await audit.record(
        event="mcp.install.uninstalled",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"workspace_id": workspace_id},
    )


@router.patch(
    "/connectors/{install_id}/state",
    response_model=MCPWorkspaceConnectorStateOut,
)
async def patch_workspace_connector_state(
    workspace_id: str,
    install_id: str,
    body: PatchWorkspaceStateIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPWorkspaceConnectorStateOut:
    """Workspace-admin-only enablement / credential-policy edit.

    The path lives under ``/connectors`` (not ``/installs``) by design —
    per spec, install-lifecycle operations stay under ``/installs`` while
    per-workspace state edits sit under the effective-connector view.
    """
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})

    current = await svc._state_repo.get(workspace_id, install_id)
    if current is None:
        # No existing state row: for workspace-local installs it should have
        # been created at install time; for org installs the admin distribution
        # must have set one. Either way a PATCH against a missing row is 404.
        raise HTTPException(404, detail={"code": "mcp_workspace_state_not_found"})

    new_policy = (
        body.credential_policy if body.credential_policy is not None else current.credential_policy
    )
    if body.credential_policy is not None:
        _validate_install_policy_pairing(
            install=install,
            requested_policy=new_policy,
            field="credential_policy",
        )
    new_enabled = body.enabled if body.enabled is not None else current.enabled

    saved = await svc._state_repo.upsert(
        workspace_id=workspace_id,
        install_id=install_id,
        enabled=new_enabled,
        credential_policy=new_policy,
        enablement_source=current.enablement_source,
        updated_by_user_id=ctx.user.id,
    )
    await audit.record(
        event="mcp.workspace_state.patched",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"workspace_id": workspace_id},
    )
    return MCPWorkspaceConnectorStateOut(
        workspace_id=saved.workspace_id,
        install_id=saved.install_id,
        enabled=saved.enabled,
        credential_policy=saved.credential_policy,  # type: ignore[arg-type]
        enablement_source=saved.enablement_source,
    )


# ---------------- workspace + user grants ---------------- #


@router.post(
    "/installs/{install_id}/grants/me",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_user_grant(
    workspace_id: str,
    install_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPCredentialGrantStatusOut:
    if body.credential_plaintext is None:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "credential_plaintext"],
                    "msg": "credential_plaintext required for static user grants",
                    "input": None,
                }
            ],
        )
    try:
        grant = await svc.create_static_grant(
            install_id=install_id,
            grant_scope="user",
            plaintext=body.credential_plaintext,
            workspace_id=workspace_id,
            user_id=ctx.user.id,
            name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_grant", "msg": str(exc)}) from exc
    await audit.record(
        event="mcp.grant.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "user", "workspace_id": workspace_id},
    )
    return MCPCredentialGrantStatusOut(
        install_id=install_id,
        grant_scope="user",
        workspace_id=workspace_id,
        user_id=ctx.user.id,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{install_id}/grants/me",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_my_user_grant(
    workspace_id: str,
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(
        install_id=install_id,
        grant_scope="user",
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "user", "workspace_id": workspace_id},
    )


@router.post(
    "/installs/{install_id}/grants/me/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def my_user_grant_oauth_start(
    workspace_id: str,
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — present for OpenAPI clarity
    _ctx: Annotated[RequestContext, Depends(require_member)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a user-scope grant."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "mcp_oauth.four_layer_start_not_yet_wired",
            "message": (
                "Four-layer OAuth start is registered but the AS handshake"
                " wiring lands in plan Task 6."
            ),
            "install_id": install_id,
            "workspace_id": workspace_id,
        },
    )


@router.post(
    "/installs/{install_id}/grants/workspace",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_grant(
    workspace_id: str,
    install_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPCredentialGrantStatusOut:
    if body.credential_plaintext is None:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "credential_plaintext"],
                    "msg": "credential_plaintext required for static workspace grants",
                    "input": None,
                }
            ],
        )
    try:
        grant = await svc.create_static_grant(
            install_id=install_id,
            grant_scope="workspace",
            plaintext=body.credential_plaintext,
            workspace_id=workspace_id,
            name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_grant", "msg": str(exc)}) from exc
    await audit.record(
        event="mcp.grant.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "workspace", "workspace_id": workspace_id},
    )
    return MCPCredentialGrantStatusOut(
        install_id=install_id,
        grant_scope="workspace",
        workspace_id=workspace_id,
        user_id=None,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{install_id}/grants/workspace",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_grant(
    workspace_id: str,
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(
        install_id=install_id,
        grant_scope="workspace",
        workspace_id=workspace_id,
    )
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"scope": "workspace", "workspace_id": workspace_id},
    )


@router.post(
    "/installs/{install_id}/grants/workspace/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def workspace_grant_oauth_start(
    workspace_id: str,
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — present for OpenAPI clarity
    _ctx: Annotated[RequestContext, Depends(require_admin)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a workspace-scope grant."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "mcp_oauth.four_layer_start_not_yet_wired",
            "message": (
                "Four-layer OAuth start is registered but the AS handshake"
                " wiring lands in plan Task 6."
            ),
            "install_id": install_id,
            "workspace_id": workspace_id,
        },
    )
