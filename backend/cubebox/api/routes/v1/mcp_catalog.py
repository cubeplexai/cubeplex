"""MCP catalog routes — Phase 3 of the catalog + OAuth design.

Catalog-centric grouping: this module owns every route that touches a
catalog connector or a catalog-backed install row, regardless of whether
it's an org-admin or workspace-member entry point. The handcrafted
``/admin/mcp/servers`` URL/transport CRUD lives in ``admin_mcp.py`` and
is retained as an advanced/debug surface — see the docstring on
``admin_mcp.create_server``.

Routers (mounted with ``/api/v1`` prefix in ``api/app.py``):

- ``catalog_admin_router`` — ``/admin/mcp/catalog/...`` and
  ``/admin/mcp/installs/...`` (org admin install lifecycle)
- ``catalog_member_router`` — ``/ws/{workspace_id}/mcp/catalog`` (read)
  and ``/ws/{workspace_id}/mcp/catalog/{catalog_id}/install`` +
  ``/ws/{workspace_id}/mcp/installs/{install_id}`` +
  ``/ws/{workspace_id}/mcp/org-installs/{install_id}/override``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from cubebox.api.schemas.mcp import (
    MCPCatalogConnectorOut,
    MCPCatalogInstallIn,
    MCPCatalogInstallOut,
    MCPCatalogInstallWsIn,
    MCPCatalogListOut,
    MCPInstallSwitchAuthIn,
    MCPOrgInstallOverrideIn,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.mcp.dependencies import (
    get_admin_catalog_service,
    get_admin_request_context,
    get_audit_sink,
    get_mcp_service,
    get_member_catalog_service,
)
from cubebox.mcp.exceptions import (
    MCPCatalogAuthMethodUnsupported,
    MCPCatalogConnectorNotFound,
    MCPCatalogInstallExists,
    MCPCredentialRequired,
    MCPServerNotFound,
    MCPUserScopeCredentialForbidden,
)
from cubebox.services.mcp import MCPServerService
from cubebox.services.mcp_catalog import CatalogConnectorDTO, InstallResult, MCPCatalogService

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Admin install/delete/switch-auth lives under /admin/mcp/...
catalog_admin_router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp-catalog"])

# Member-scoped catalog read + workspace install/override/delete live under
# /ws/{workspace_id}/mcp/...
catalog_member_router = APIRouter(prefix="/ws/{workspace_id}/mcp", tags=["workspace-mcp-catalog"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connector_to_out(dto: CatalogConnectorDTO) -> MCPCatalogConnectorOut:
    connector = dto.connector
    return MCPCatalogConnectorOut(
        id=connector.id,
        slug=connector.slug,
        name=connector.name,
        provider=connector.provider,
        description=connector.description,
        server_url=connector.server_url,
        transport=connector.transport,
        supported_auth_methods=list(connector.supported_auth_methods),
        default_credential_scope=connector.default_credential_scope,
        oauth_dcr_supported=connector.oauth_dcr_supported,
        oauth_default_scope=connector.oauth_default_scope,
        static_form_fields=connector.static_form_fields,
        metadata=dict(connector.cred_metadata or {}),
        status=connector.status,
        org_install_id=dto.org_install_id,
        workspace_visible=dto.workspace_visible,
        user_install_id=dto.user_install_id,
    )


def _install_result_to_out(
    result: InstallResult,
    *,
    authed: bool,
) -> MCPCatalogInstallOut:
    return MCPCatalogInstallOut(
        install_id=result.install_id,
        requires_oauth=result.requires_oauth,
        authed=authed,
    )


def _map_install_exception(exc: Exception) -> HTTPException:
    """Map service-layer exceptions to HTTP responses for install endpoints.

    Every catalog HTTPException uses the ``{"code", "message"}`` envelope
    so the frontend can branch on a stable machine-readable string while
    still surfacing a human-readable hint.
    """
    if isinstance(exc, MCPCatalogConnectorNotFound):
        return HTTPException(
            404,
            detail={
                "code": "mcp_catalog.connector_not_found",
                "message": "Catalog connector not found.",
            },
        )
    if isinstance(exc, MCPCatalogAuthMethodUnsupported):
        return HTTPException(
            400,
            detail={
                "code": "mcp_catalog.auth_method_unsupported",
                "message": f"auth_method not in supported_auth_methods: {exc}",
            },
        )
    if isinstance(exc, MCPCatalogInstallExists):
        return HTTPException(
            409,
            detail={
                "code": "mcp_catalog.install_exists",
                "message": "install already exists for this catalog connector",
            },
        )
    if isinstance(exc, MCPCredentialRequired):
        return HTTPException(
            400,
            detail={
                "code": "mcp_catalog.credential_required",
                "message": "auth_method requires a credential, but none was supplied.",
            },
        )
    if isinstance(exc, MCPUserScopeCredentialForbidden):
        return HTTPException(
            400,
            detail={
                "code": "mcp_catalog.user_scope_credential_forbidden",
                "message": "user-scope installs cannot accept a plaintext credential here.",
            },
        )
    if isinstance(exc, MCPServerNotFound):
        return HTTPException(
            404,
            detail={
                "code": "mcp_catalog.install_not_found",
                "message": "MCP install not found.",
            },
        )
    return HTTPException(
        500,
        detail={
            "code": "mcp_catalog.internal_error",
            "message": "Unexpected internal error in MCP catalog service.",
        },
    )


async def _server_authed(svc: MCPCatalogService, install_id: str) -> bool:
    server = await svc.server_repo.get(install_id)
    return bool(server is not None and server.authed)


# ---------------------------------------------------------------------------
# 3.1 — GET /api/v1/ws/{workspace_id}/mcp/catalog
# ---------------------------------------------------------------------------


@catalog_member_router.get("/catalog", response_model=MCPCatalogListOut)
async def list_catalog(
    workspace_id: str = Path(..., max_length=20),
    q: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    svc: MCPCatalogService = Depends(get_member_catalog_service),
) -> MCPCatalogListOut:
    """Member-readable catalog with per-(workspace, user) install status.

    v1 only surfaces active connectors. Spec §5.1 lists ``status`` as a
    future query param for an audit / cleanup UI; until that surface
    exists we don't accept the param at all (rather than ship a stub
    that silently returns an empty list for any non-``active`` value).
    """
    dtos = await svc.list_for_member(workspace_id, q=q, provider=provider)
    return MCPCatalogListOut(items=[_connector_to_out(dto) for dto in dtos])


# ---------------------------------------------------------------------------
# 3.1b — GET /api/v1/ws/{workspace_id}/mcp/catalog/{slug}/tool-citations
# ---------------------------------------------------------------------------


class CatalogToolCitationsResponse(BaseModel):
    slug: str
    tool_citations: dict[str, dict[str, Any]]


@catalog_member_router.get("/catalog/{slug}/tool-citations")
async def get_catalog_tool_citations(
    slug: str,
    workspace_id: str = Path(..., max_length=20),
    svc: MCPCatalogService = Depends(get_member_catalog_service),
) -> CatalogToolCitationsResponse:
    """Return the catalog connector's default tool_citations for a given slug.

    Used by the frontend editor's "Reset to catalog default" button. The
    catalog is org-agnostic but the route lives under /ws/{workspace_id}/
    so workspace-member auth is enforced by ``get_member_catalog_service``.
    """
    row = await svc.catalog_repo.get_by_slug(slug)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "mcp_catalog.connector_not_found",
                "message": "Catalog connector not found.",
            },
        )
    return CatalogToolCitationsResponse(
        slug=row.slug,
        tool_citations=dict(row.tool_citations or {}),
    )


# ---------------------------------------------------------------------------
# 3.2 — POST /api/v1/admin/mcp/catalog/{catalog_id}/install
# ---------------------------------------------------------------------------


@catalog_admin_router.post(
    "/catalog/{catalog_id}/install",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPCatalogInstallOut,
)
async def install_for_org(
    catalog_id: str,
    body: MCPCatalogInstallIn,
    svc: MCPCatalogService = Depends(get_admin_catalog_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPCatalogInstallOut:
    """Org admin installs a catalog connector org-wide.

    The admin endpoint is org-wide only — user-scope installs go through
    the workspace endpoint (``POST /api/v1/ws/{ws}/mcp/catalog/{id}/install``)
    like any member.
    """
    try:
        result = await svc.install_for_org(
            catalog_id=catalog_id,
            auth_method=body.auth_method,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
            auto_enable_workspaces=body.auto_enable_workspaces,
        )
    except (
        MCPCatalogConnectorNotFound,
        MCPCatalogAuthMethodUnsupported,
        MCPCatalogInstallExists,
        MCPCredentialRequired,
        MCPUserScopeCredentialForbidden,
    ) as exc:
        raise _map_install_exception(exc) from exc

    authed = await _server_authed(svc, result.install_id)
    await audit.record(
        event="mcp.catalog.installed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=result.install_id,
        details={
            "catalog_id": catalog_id,
            "auth_method": body.auth_method,
        },
    )
    return _install_result_to_out(result, authed=authed)


# ---------------------------------------------------------------------------
# 3.3 — DELETE /api/v1/admin/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


@catalog_admin_router.delete(
    "/installs/{install_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_org_install(
    install_id: str,
    svc: MCPCatalogService = Depends(get_admin_catalog_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> None:
    """Soft-disable an org install (keeps row, clears credentials, authed=false)."""
    try:
        await svc.delete_install(install_id)
    except MCPServerNotFound as exc:
        raise _map_install_exception(exc) from exc

    await audit.record(
        event="mcp.catalog.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
    )


# ---------------------------------------------------------------------------
# 3.4 — PATCH /api/v1/admin/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


@catalog_admin_router.patch(
    "/installs/{install_id}",
    response_model=MCPCatalogInstallOut,
)
async def switch_org_install_auth(
    install_id: str,
    body: MCPInstallSwitchAuthIn,
    svc: MCPCatalogService = Depends(get_admin_catalog_service),
    ctx: RequestContext = Depends(get_admin_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPCatalogInstallOut:
    """Re-key an existing install with a new auth_method."""
    try:
        result = await svc.switch_auth_method(
            install_id=install_id,
            new_auth_method=body.auth_method,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
        )
    except (
        MCPServerNotFound,
        MCPCatalogConnectorNotFound,
        MCPCatalogAuthMethodUnsupported,
        MCPCredentialRequired,
        MCPUserScopeCredentialForbidden,
    ) as exc:
        raise _map_install_exception(exc) from exc

    authed = await _server_authed(svc, result.install_id)
    await audit.record(
        event="mcp.catalog.auth_switched",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"auth_method": body.auth_method},
    )
    return _install_result_to_out(result, authed=authed)


# ---------------------------------------------------------------------------
# 3.5 — POST /api/v1/ws/{workspace_id}/mcp/catalog/{catalog_id}/install
# ---------------------------------------------------------------------------


@catalog_member_router.post(
    "/catalog/{catalog_id}/install",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPCatalogInstallOut,
)
async def install_for_workspace(
    catalog_id: str,
    body: MCPCatalogInstallWsIn,
    workspace_id: str = Path(..., max_length=20),
    svc: MCPCatalogService = Depends(get_member_catalog_service),
    ctx: RequestContext = Depends(require_member),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPCatalogInstallOut:
    """Workspace user self-installs a catalog connector. ``scope`` is forced
    to ``user`` (workspace-private, ``credential_scope=user``).
    """
    try:
        result = await svc.install_for_workspace(
            catalog_id=catalog_id,
            workspace_id=workspace_id,
            auth_method=body.auth_method,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
        )
    except (
        MCPCatalogConnectorNotFound,
        MCPCatalogAuthMethodUnsupported,
        MCPCatalogInstallExists,
        MCPCredentialRequired,
        MCPUserScopeCredentialForbidden,
    ) as exc:
        raise _map_install_exception(exc) from exc

    authed = await _server_authed(svc, result.install_id)
    await audit.record(
        event="mcp.catalog.workspace_installed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=result.install_id,
        details={
            "catalog_id": catalog_id,
            "workspace_id": workspace_id,
            "auth_method": body.auth_method,
        },
    )
    return _install_result_to_out(result, authed=authed)


# ---------------------------------------------------------------------------
# 3.6 — PATCH /api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override
# ---------------------------------------------------------------------------


@catalog_member_router.patch(
    "/org-installs/{install_id}/override",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def patch_org_install_override(
    install_id: str,
    body: MCPOrgInstallOverrideIn,
    workspace_id: str = Path(..., max_length=20),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
) -> None:
    """Toggle visibility of an org-wide install for the calling workspace.

    ``enabled=True`` upserts an enabled row (makes it visible);
    ``enabled=False`` deletes the override row (default-invisible).
    Workspace-private installs return 400 — there's nothing to override.
    """
    server = await svc.server_repo.get(install_id)
    if server is None:
        raise HTTPException(
            404,
            detail={
                "code": "mcp_catalog.install_not_found",
                "message": "MCP install not found.",
            },
        )
    if server.org_id != ctx.org_id:
        # Don't leak existence to other orgs.
        raise HTTPException(
            404,
            detail={
                "code": "mcp_catalog.install_not_found",
                "message": "MCP install not found.",
            },
        )
    if server.owner_workspace_id is not None:
        raise HTTPException(
            400,
            detail={
                "code": "mcp_catalog.workspace_owned_no_override",
                "message": "Workspace-private installs have no org-level override.",
            },
        )

    if body.enabled:
        # New semantics: enabled=True creates a visible override row.
        await svc.override_repo.upsert(
            workspace_id=workspace_id,
            mcp_server_id=install_id,
            enabled=True,
            updated_by_user_id=ctx.user.id,
        )
    else:
        # Disabling = delete the override row (no row = invisible).
        await svc.override_repo.delete(workspace_id=workspace_id, mcp_server_id=install_id)


# ---------------------------------------------------------------------------
# 3.7 — DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


@catalog_member_router.delete(
    "/installs/{install_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_install(
    install_id: str,
    workspace_id: str = Path(..., max_length=20),
    svc: MCPCatalogService = Depends(get_member_catalog_service),
    ctx: RequestContext = Depends(require_member),
    audit: AuditSink = Depends(get_audit_sink),
) -> None:
    """Workspace user deletes their own workspace-private install.

    Permission: only the install creator may delete (admins use the
    ``/admin/mcp/installs/{id}`` route for any install in their org).
    """
    server = await svc.server_repo.get(install_id)
    if server is None or server.owner_workspace_id != workspace_id:
        raise HTTPException(
            404,
            detail={
                "code": "mcp_catalog.install_not_found",
                "message": "MCP install not found.",
            },
        )
    if server.created_by_user_id != ctx.user.id:
        raise HTTPException(
            403,
            detail={
                "code": "mcp_catalog.permission_denied",
                "message": "Only the install creator may delete this workspace install.",
            },
        )

    try:
        await svc.delete_install(install_id)
    except MCPServerNotFound as exc:
        raise HTTPException(
            404,
            detail={
                "code": "mcp_catalog.install_not_found",
                "message": "MCP install not found.",
            },
        ) from exc

    await audit.record(
        event="mcp.catalog.workspace_deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"workspace_id": workspace_id},
    )
