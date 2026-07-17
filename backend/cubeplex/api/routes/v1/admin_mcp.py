"""Admin MCP routes: template-centric catalog surface.

Routes under ``/admin/mcp/{catalog,templates,...}`` operate on the
template-centric model — ``MCPConnectorTemplate`` / ``MCPConnector`` /
``MCPCredentialGrant``.

Kept as-is (connector_id-keyed, unchanged):
  installs/{id}/grants/org, /oauth/start, /refresh-discovery, /invoke,
  /test-connection, /tool-citations, PATCH /installs/{id}.

Removed:
  POST /installs, POST /promote-to-org, GET /connectors,
  GET /installs/{id}/effective, GET /admin/mcp/templates (list),
  GET /mcp/templates (public list — catalog endpoints replace both).
"""

import asyncio
import time
from contextvars import ContextVar
from typing import Annotated, Any, cast

import httpx
from cubepi.mcp import load_mcp_tools_http
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.api.schemas.mcp import (
    AdminInstallInvokeIn,
    AdminInstallRefreshIn,
    CreateGrantIn,
    MCPConnectorOut,
    MCPCredentialGrantStatusOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPToolEntry,
    PatchInstallIn,
    TestConnectionIn,
    TestConnectionOut,
    ToolCitationUpsertIn,
    ToolInvokeOut,
)
from cubeplex.api.schemas.mcp_catalog import (
    AdminCatalogListOut,
    AdminCatalogRowOut,
    CreateTemplateIn,
    DistributeIn,
    MCPConnectorFactsOut,
    MCPTemplateOut,
    UpdateTemplateIn,
)
from cubeplex.audit.sink import AuditSink
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import current_active_user
from cubeplex.credentials.dependencies import build_credential_service, get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.mcp.dependencies import (
    get_admin_install_service,
    get_admin_oauth_token_manager,
    get_admin_request_context,
    get_audit_sink,
    get_grant_repo,
    get_oauth_start_service,
    get_user_token_signer,
)
from cubeplex.mcp.exceptions import MCPDiscoveryFailed
from cubeplex.mcp.oauth import OAuthStartError, OAuthStartService
from cubeplex.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubeplex.mcp.oauth.token_manager import OAuthTokenManager
from cubeplex.mcp.user_token import MCPUserTokenSigner
from cubeplex.models import MCPConnector, User
from cubeplex.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubeplex.repositories.workspace import WorkspaceRepository
from cubeplex.services.mcp_catalog import AdminCatalogRow, build_admin_catalog_rows
from cubeplex.services.mcp_discovery import (
    discover_tools_for_install,
    run_post_grant_discovery,
)
from cubeplex.services.mcp_installs import MCPConnectorService

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _template_to_out(template: Any) -> MCPTemplateOut:
    from cubeplex.mcp.icons import template_icon_key

    return MCPTemplateOut(
        template_id=template.id,
        slug=template.slug,
        name=template.name,
        provider=template.provider,
        description=template.description,
        scope=template.scope,
        workspace_id=template.workspace_id,
        server_url=template.server_url,
        transport=template.transport,
        supported_auth_methods=list(template.supported_auth_methods or []),
        default_credential_policy=template.default_credential_policy,
        status=template.status,
        icon=template_icon_key(getattr(template, "template_metadata", None)),
    )


async def _guard_template_url_taken(
    template_repo: MCPConnectorTemplateRepository,
    *,
    org_id: str,
    server_url: str,
    exclude_template_id: str | None,
) -> None:
    """Reject template create / edit when the URL is already used by another
    active template in the same org (or a global one). Returns 409
    ``server_url_taken_in_org`` — same shape the state/distribute routes emit,
    so the frontend's error translator covers this too."""
    colliding = await template_repo.find_active_template_by_url(
        org_id=org_id,
        server_url=server_url,
        exclude_template_id=exclude_template_id,
    )
    if colliding is not None:
        raise HTTPException(
            409,
            detail={
                "code": "server_url_taken_in_org",
                "colliding_template_name": colliding.name,
                "server_url": server_url,
            },
        )


async def _guard_connectivity_edit(
    session: AsyncSession,
    org_id: str,
    template_id: str,
    body: UpdateTemplateIn,
) -> None:
    """Raise 409 ``template_in_use`` if the request changes connectivity-affecting
    fields while an active connector exists for the template. Mirrors the
    delete pre-condition — users must Purge first."""
    if body.server_url is None and body.transport is None:
        return
    connector_repo = MCPConnectorRepository(session, org_id=org_id)
    connector = await connector_repo.get_by_template_id(template_id)
    if connector is not None:
        raise HTTPException(409, detail={"code": "template_in_use"})


def _updated_fields(body: UpdateTemplateIn) -> list[str]:
    return [k for k in ("name", "server_url", "transport") if getattr(body, k) is not None]


def _connector_to_facts(
    connector: MCPConnector,
    *,
    org_grant_auth_method: str | None = None,
) -> MCPConnectorFactsOut:
    from cubeplex.api.schemas.mcp import McpIconOut
    from cubeplex.mcp.icons import server_icons_from_discovery

    tools_cache = connector.tools_cache or []
    tool_entries = [
        MCPToolEntry(
            name=str(t.get("name", "")),
            description=t.get("description"),
            input_schema=t.get("input_schema"),
        )
        for t in tools_cache
        if isinstance(t, dict) and t.get("name")
    ]
    server_icons = [
        McpIconOut.model_validate(icon)
        for icon in server_icons_from_discovery(getattr(connector, "discovery_metadata", None))
    ]
    return MCPConnectorFactsOut(
        connector_id=connector.id,
        default_credential_policy=connector.default_credential_policy,
        discovery_status=connector.discovery_status,
        tool_count=len(tool_entries),
        tools=tool_entries,
        tool_citations=dict(connector.tool_citations or {}),
        last_error=connector.last_error,
        auto_enroll_new_workspaces=connector.auto_enroll_new_workspaces,
        org_grant_auth_method=org_grant_auth_method,  # type: ignore[arg-type]
        server_icons=server_icons,
    )


def _install_to_out(
    install: MCPConnector,
    *,
    connector_id: str,
) -> MCPConnectorOut:
    """Serialise MCPConnector → MCPConnectorOut."""
    from cubeplex.api.schemas.mcp import McpIconOut
    from cubeplex.mcp.icons import server_icons_from_discovery

    tools_cache = install.tools_cache or []
    tool_entries = [
        MCPToolEntry(
            name=str(t.get("name", "")),
            description=t.get("description"),
            input_schema=t.get("input_schema"),
        )
        for t in tools_cache
        if isinstance(t, dict) and t.get("name")
    ]
    server_icons = [
        McpIconOut.model_validate(icon)
        for icon in server_icons_from_discovery(getattr(install, "discovery_metadata", None))
    ]
    return MCPConnectorOut(
        connector_id=connector_id,
        template_id=install.template_id,
        name=install.name,
        server_url=install.server_url,
        transport=install.transport,
        default_credential_policy=cast("Any", install.default_credential_policy),
        discovery_status=install.discovery_status,
        status=install.status,
        tool_count=len(tool_entries),
        tools=tool_entries,
        tool_citations=dict(install.tool_citations or {}),
        last_error=install.last_error,
        auto_enroll_new_workspaces=install.auto_enroll_new_workspaces,
        server_icons=server_icons,
    )


def _row_to_out(row: AdminCatalogRow) -> AdminCatalogRowOut:
    grant_status: str | None
    if row.org_grant_status == "expired":
        grant_status = "expired"
    elif row.org_grant_status == "valid":
        grant_status = "valid"
    else:
        grant_status = None

    return AdminCatalogRowOut(
        template=_template_to_out(row.template),
        connector=(
            _connector_to_facts(
                row.connector,
                org_grant_auth_method=row.org_grant_auth_method,
            )
            if row.connector is not None
            else None
        ),
        disabled=row.disabled,
        in_use=row.in_use,
        needs_attention=row.needs_attention,
        enabled_workspace_count=row.enabled_workspace_count,
        eligible_workspace_count=row.eligible_workspace_count,
        org_grant_status=grant_status,  # type: ignore[arg-type]
    )


async def _assemble_admin_row(
    *,
    template_id: str,
    session: AsyncSession,
    grant_repo: MCPCredentialGrantRepository,
    ctx: RequestContext,
) -> AdminCatalogRowOut:
    """Build a single AdminCatalogRowOut — reused by distribute and the catalog handler."""
    template_repo = MCPConnectorTemplateRepository(session)
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)
    workspace_repo = WorkspaceRepository(session)

    template = await template_repo.get(template_id)
    if template is None:
        raise HTTPException(404, detail={"code": "template_not_found"})

    connector = await connector_repo.get_by_template_id(template_id)
    connectors_by_template = {template_id: connector} if connector is not None else {}
    enabled_counts: dict[str, int] = {}
    org_grants: dict[str, Any] = {}
    if connector is not None:
        rows = await state_repo.list_for_install(connector.id)
        enabled_counts[connector.id] = sum(1 for r in rows if r.enabled)
        org_grants[connector.id] = await grant_repo.get_org_grant(connector.id)

    catalog_rows = build_admin_catalog_rows(
        templates=[template],
        connectors_by_template_id=connectors_by_template,
        disabled_template_ids=await settings_repo.disabled_template_ids(),
        enabled_counts_by_connector_id=enabled_counts,
        org_grants_by_connector_id=org_grants,
        eligible_workspace_count=len(await workspace_repo.list_for_org(ctx.org_id)),
    )
    return _row_to_out(catalog_rows[0])


# ---------------------------------------------------------------------------
# Catalog handler (GET /admin/mcp/catalog)
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=AdminCatalogListOut)
async def admin_catalog(
    session: Annotated[AsyncSession, Depends(get_session)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> AdminCatalogListOut:
    """Admin template catalog: every template visible to this org with effective state."""
    template_repo = MCPConnectorTemplateRepository(session)
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)
    workspace_repo = WorkspaceRepository(session)

    templates = await template_repo.list_visible_for_org(ctx.org_id)
    connectors = await connector_repo.list_active()
    connectors_by_template: dict[str, Any] = {c.template_id: c for c in connectors}
    enabled_counts: dict[str, int] = {}
    org_grants: dict[str, Any] = {}
    for connector in connectors:
        rows = await state_repo.list_for_install(connector.id)
        enabled_counts[connector.id] = sum(1 for r in rows if r.enabled)
        org_grants[connector.id] = await grant_repo.get_org_grant(connector.id)
    catalog_rows = build_admin_catalog_rows(
        templates=templates,
        connectors_by_template_id=connectors_by_template,
        disabled_template_ids=await settings_repo.disabled_template_ids(),
        enabled_counts_by_connector_id=enabled_counts,
        org_grants_by_connector_id=org_grants,
        eligible_workspace_count=len(await workspace_repo.list_for_org(ctx.org_id)),
    )
    return AdminCatalogListOut(items=[_row_to_out(r) for r in catalog_rows])


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/templates",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPTemplateOut,
)
async def create_template(
    body: CreateTemplateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPTemplateOut:
    """Create an org-scoped custom MCP template."""
    template_repo = MCPConnectorTemplateRepository(session)
    await _guard_template_url_taken(
        template_repo,
        org_id=ctx.org_id,
        server_url=body.server_url,
        exclude_template_id=None,
    )
    try:
        template = await template_repo.create_scoped(
            scope="org",
            org_id=ctx.org_id,
            workspace_id=None,
            created_by_user_id=ctx.user.id,
            name=body.name,
            server_url=body.server_url,
            transport=body.transport,
            supported_auth_methods=[body.auth_method],
            default_credential_policy=body.default_credential_policy,
        )
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(409, detail={"code": code}) from exc
    await audit.record(
        event="mcp.template.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template.id,
        details={"scope": "org", "name": body.name},
    )
    return _template_to_out(template)


@router.patch(
    "/templates/{template_id}",
    response_model=MCPTemplateOut,
)
async def update_template(
    template_id: str,
    body: UpdateTemplateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPTemplateOut:
    """Edit an org-owned custom MCP template.

    Only templates with ``scope='org'`` and ``org_id == ctx.org_id`` may be
    edited. ``name`` is always editable; ``server_url`` / ``transport`` require
    no active connector (409 ``template_in_use``) — user must Purge first.
    """
    template_repo = MCPConnectorTemplateRepository(session)
    template = await template_repo.get(template_id)
    if template is None or template.scope != "org" or template.org_id != ctx.org_id:
        raise HTTPException(404, detail={"code": "template_not_owned_by_org"})

    await _guard_connectivity_edit(session, ctx.org_id, template_id, body)
    if body.server_url is not None and body.server_url != template.server_url:
        await _guard_template_url_taken(
            template_repo,
            org_id=ctx.org_id,
            server_url=body.server_url,
            exclude_template_id=template_id,
        )

    try:
        updated = await template_repo.update_custom_fields(
            template,
            name=body.name,
            server_url=body.server_url,
            transport=body.transport,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

    await audit.record(
        event="mcp.template.updated",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={
            "scope": "org",
            "changed": _updated_fields(body),
        },
    )
    return _template_to_out(updated)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_template(
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    """Delete an org-owned template.

    Only templates with ``scope='org'`` and ``org_id == ctx.org_id`` may be
    deleted. Global templates and templates owned by another org return 404.
    409 if an active connector exists for this template.
    """
    template_repo = MCPConnectorTemplateRepository(session)
    template = await template_repo.get(template_id)
    if template is None or template.scope != "org" or template.org_id != ctx.org_id:
        raise HTTPException(404, detail={"code": "template_not_owned_by_org"})

    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    connector = await connector_repo.get_by_template_id(template_id)
    if connector is not None:
        raise HTTPException(409, detail={"code": "template_in_use"})

    template.status = "deleted"
    await session.commit()

    await audit.record(
        event="mcp.template.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
    )


# ---------------------------------------------------------------------------
# Disable / re-enable
# ---------------------------------------------------------------------------


@router.put(
    "/templates/{template_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disable_template(
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    """Disable a template for this org (workspace catalog hides it)."""
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)
    await settings_repo.set_disabled(template_id, True, updated_by_user_id=ctx.user.id)
    await audit.record(
        event="mcp.template.disabled",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={"disabled": True},
    )


@router.delete(
    "/templates/{template_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reenable_template(
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    """Re-enable a previously disabled template."""
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)
    await settings_repo.set_disabled(template_id, False, updated_by_user_id=ctx.user.id)
    await audit.record(
        event="mcp.template.disabled",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={"disabled": False},
    )


# ---------------------------------------------------------------------------
# Distribute
# ---------------------------------------------------------------------------


@router.post(
    "/templates/{template_id}/distribute",
    response_model=AdminCatalogRowOut,
)
async def distribute_template(
    template_id: str,
    body: DistributeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> AdminCatalogRowOut:
    """Ensure a connector exists + fan out state rows to workspaces.

    Returns the refreshed AdminCatalogRowOut for this template.
    """
    template_repo = MCPConnectorTemplateRepository(session)
    template = await template_repo.get(template_id)
    if template is None:
        raise HTTPException(404, detail={"code": "template_not_found"})

    try:
        await svc.distribute(
            template,
            enable_existing=body.enable_existing,
            auto_enroll=body.auto_enroll,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "server_url_taken_in_org":
            collision_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
            colliding_name = await collision_repo.find_colliding_template_name(
                url=template.server_url, exclude_template_id=template_id
            )
            raise HTTPException(
                409,
                detail={
                    "code": "server_url_taken_in_org",
                    "colliding_template_name": colliding_name,
                    "server_url": template.server_url,
                },
            ) from exc
        raise
    await audit.record(
        event="mcp.template.distributed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={"enable_existing": body.enable_existing, "auto_enroll": body.auto_enroll},
    )
    return await _assemble_admin_row(
        template_id=template_id,
        session=session,
        grant_repo=grant_repo,
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


@router.post(
    "/templates/{template_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def purge_template(
    template_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    """Hard-delete the connector for this template (state rows + grants included)."""
    try:
        await svc.purge(template_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    await audit.record(
        event="mcp.template.purged",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
    )


# ---------------------------------------------------------------------------
# Existing connector-keyed endpoints kept as-is
# ---------------------------------------------------------------------------


@router.get(
    "/installs/{connector_id}",
    response_model=MCPConnectorOut,
)
async def get_admin_install(
    connector_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
) -> MCPConnectorOut:
    install = await svc._install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    connector_id = await svc._connector_id_for_install(install) or ""
    return _install_to_out(install, connector_id=connector_id)


async def _reject_if_template_disabled(
    session: AsyncSession, *, org_id: str, template_id: str
) -> None:
    """Raise 409 if the template is org-disabled.  Call after loading the connector."""
    settings_repo = MCPTemplateSettingsRepository(session, org_id=org_id)
    disabled_ids = await settings_repo.disabled_template_ids()
    if template_id in disabled_ids:
        raise HTTPException(409, detail={"code": "template_disabled_in_org"})


@router.post(
    "/installs/{connector_id}/refresh-discovery",
    response_model=MCPConnectorOut,
)
async def admin_refresh_discovery(
    connector_id: str,
    body: AdminInstallRefreshIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_admin_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorOut:
    """Re-discover tools for one install and persist into ``tools_cache``."""
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
    )
    install_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    await _reject_if_template_disabled(session, org_id=ctx.org_id, template_id=install.template_id)
    effective_policy = install.default_credential_policy
    if body.workspace_id:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
        ws_state = await state_repo.get(body.workspace_id, connector_id)
        if ws_state is not None and ws_state.credential_policy:
            effective_policy = ws_state.credential_policy
    needs_ws = effective_policy in {"workspace", "user"}
    if needs_ws and not body.workspace_id:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "workspace_id"],
                    "msg": "workspace_id_required_for_scoped_policy",
                    "input": None,
                }
            ],
        )
    try:
        await discover_tools_for_install(
            connector_id=connector_id,
            workspace_id=body.workspace_id,
            actor_user_id=ctx.user.id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,
        )
    except MCPDiscoveryFailed as exc:
        raise HTTPException(
            400, detail={"code": "connector_not_usable", "reason": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc)}) from exc
    refreshed = await install_repo.get(connector_id)
    assert refreshed is not None
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    cid = await connector_repo.get_connector_id_for_install(refreshed) or ""
    return _install_to_out(refreshed, connector_id=cid)


@router.put(
    "/installs/{connector_id}/tool-citations",
    response_model=MCPConnectorOut,
)
async def admin_upsert_tool_citation(
    connector_id: str,
    body: ToolCitationUpsertIn,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorOut:
    """Upsert or clear one tool's citation mapping on an install."""
    install = await svc._install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    current = dict(install.tool_citations or {})
    if body.config is None:
        current.pop(body.tool_name, None)
    else:
        current[body.tool_name] = body.config
    install.tool_citations = current
    saved = await svc._install_repo.update(install)
    connector_id = await svc._connector_id_for_install(saved) or ""
    return _install_to_out(saved, connector_id=connector_id)


# ---------------------------------------------------------------------------
# Try It (admin surface).
# ---------------------------------------------------------------------------


_INVOKE_USER_ID_ADMIN: ContextVar[str | None] = ContextVar("_INVOKE_USER_ID_ADMIN", default=None)
_ADMIN_INVOKE_TIMEOUT_SECONDS = 10.0


def _set_admin_invoke_user_id(user: User = Depends(current_active_user)) -> User:
    _INVOKE_USER_ID_ADMIN.set(user.id)
    return user


def _admin_invoke_rate_key(_req: Request | None = None) -> str:
    return _INVOKE_USER_ID_ADMIN.get() or "anonymous"


@router.post(
    "/installs/{connector_id}/tools/{tool_name:path}/invoke",
    response_model=ToolInvokeOut,
)
@limiter.limit("30/minute", key_func=_admin_invoke_rate_key)
async def admin_invoke_tool(
    request: Request,  # noqa: ARG001
    connector_id: str,
    tool_name: str,
    body: AdminInstallInvokeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_admin_oauth_token_manager)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    _rate_key_user: Annotated[User, Depends(_set_admin_invoke_user_id)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> ToolInvokeOut:
    """Admin Try It: invoke a tool on any install in the admin's org."""
    from cubeplex.api.routes.v1.ws_mcp import _invoke_tool_via_cubepi
    from cubeplex.mcp.cubepi_runtime import _resolve_auth_from_spec
    from cubeplex.mcp.effective import MCPEffectiveConnectorService
    from cubeplex.services.mcp_discovery import _build_runtime_spec_for_discovery

    install_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    await _reject_if_template_disabled(session, org_id=ctx.org_id, template_id=install.template_id)
    effective_policy = install.default_credential_policy
    if body.workspace_id:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
        ws_state = await state_repo.get(body.workspace_id, connector_id)
        if ws_state is not None and ws_state.credential_policy:
            effective_policy = ws_state.credential_policy
    needs_ws = effective_policy in {"workspace", "user"}
    if needs_ws and not body.workspace_id:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "workspace_id"],
                    "msg": "workspace_id_required_for_scoped_policy",
                    "input": None,
                }
            ],
        )
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
    )
    grant: Any
    if body.workspace_id is not None:
        effective_svc = MCPEffectiveConnectorService(
            template_repo=MCPConnectorTemplateRepository(session),
            settings_repo=MCPTemplateSettingsRepository(session, org_id=ctx.org_id),
            install_repo=install_repo,
            state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
            grant_repo=grant_repo,
            org_id=ctx.org_id,
        )
        dtos = await effective_svc.list_for_workspace_user(
            body.workspace_id, ctx.user.id, include_unusable=True
        )
        dto = next((d for d in dtos if d.install.id == connector_id), None)
        if dto is None or not dto.usable:
            raise HTTPException(
                400,
                detail={
                    "code": "connector_not_usable",
                    "reason": dto.reason if dto else "missing",
                },
            )
        grant = dto.grant
    else:
        grant = await grant_repo.get_org_grant(connector_id)
    spec = _build_runtime_spec_for_discovery(install=install, grant=grant)
    started = time.perf_counter()
    try:
        resolved = await _resolve_auth_from_spec(
            spec=spec,
            workspace_id=body.workspace_id or "",
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            cred_service=cred_service,
            signer=signer,
            token_manager=token_mgr,
            grant_repo=grant_repo,
        )
        if resolved is None:
            raise RuntimeError("credential_resolution_returned_none")
        headers, server_url = resolved
    except Exception as exc:  # noqa: BLE001
        duration = int((time.perf_counter() - started) * 1000)
        await audit.record(
            event="mcp.tool.invoked",
            actor_user_id=ctx.user.id,
            org_id=ctx.org_id,
            target_id=connector_id,
            details={
                "tool_name": tool_name,
                "workspace_id": body.workspace_id,
                "ok": False,
                "error_kind": "credential_resolution_failed",
            },
        )
        return ToolInvokeOut(
            ok=False,
            error=f"credential_resolution_failed: {exc}"[:512],
            duration_ms=duration,
        )
    try:
        result = await asyncio.wait_for(
            _invoke_tool_via_cubepi(
                server_url,
                tool_name,
                body.arguments,
                headers=headers or None,
                timeout=install.timeout,
                transport=install.transport,
            ),
            timeout=_ADMIN_INVOKE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        duration = int((time.perf_counter() - started) * 1000)
        await audit.record(
            event="mcp.tool.invoked",
            actor_user_id=ctx.user.id,
            org_id=ctx.org_id,
            target_id=connector_id,
            details={
                "tool_name": tool_name,
                "workspace_id": body.workspace_id,
                "ok": False,
            },
        )
        return ToolInvokeOut(ok=False, error=str(exc)[:512], duration_ms=duration)
    duration = int((time.perf_counter() - started) * 1000)
    await audit.record(
        event="mcp.tool.invoked",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={
            "tool_name": tool_name,
            "workspace_id": body.workspace_id,
            "ok": True,
        },
    )
    return ToolInvokeOut(ok=True, result=result, duration_ms=duration)


# ---------------------------------------------------------------------------
# PATCH /installs/{connector_id} — reduced surface: name/headers/default_credential_policy only
# ---------------------------------------------------------------------------


def _policy_field_error(field: str, message: str) -> HTTPException:
    """Match Pydantic's 422 envelope so the API surface is uniform."""
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


def _validate_pair(auth_method: str, policy: str, *, field: str) -> None:
    """Raise canonical 422 when (auth_method, policy) pairing is invalid."""
    if policy == "none" and auth_method != "none":
        raise _policy_field_error(
            field,
            "credential_policy='none' is only valid when auth_method='none'",
        )
    if policy != "none" and auth_method == "none":
        raise _policy_field_error(
            field,
            "auth_method='none' install requires credential_policy='none'",
        )


def _validate_install_policy_pairing(
    *,
    install: MCPConnector,
    requested_policy: str,
    field: str,
) -> None:
    """Service-level companion to policy pairing validation.

    Used by PATCH endpoints where the body alone is insufficient (auth_method
    is fixed on the row and not in the request body). Kept for ws_mcp.py
    compatibility until Task 10 rewrites that module.

    auth_method is no longer a direct field on MCPConnector (it moved to the
    template layer). Use getattr with fallback to avoid AttributeError; the
    pairing constraint is enforced at template-create time so this is a
    belt-and-suspenders guard.
    """
    auth_method: str = getattr(install, "auth_method", "none")
    _validate_pair(auth_method, requested_policy, field=field)


@router.patch(
    "/installs/{connector_id}",
    response_model=MCPConnectorOut,
)
async def patch_admin_install(
    connector_id: str,
    body: PatchInstallIn,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorOut:
    install = await svc._install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    await _reject_if_template_disabled(
        svc._install_repo.session, org_id=ctx.org_id, template_id=install.template_id
    )

    new_policy = (
        body.default_credential_policy
        if body.default_credential_policy is not None
        else install.default_credential_policy
    )
    # auth_method moved to the template layer; use getattr for compat until Task 10.
    _validate_pair(
        getattr(install, "auth_method", "none"), new_policy, field="default_credential_policy"
    )
    if body.default_credential_policy is not None:
        install.default_credential_policy = body.default_credential_policy
    if body.headers is not None:
        install.headers = body.headers
    if body.name is not None:
        install.name = body.name

    # Preflight uniqueness check for name changes
    if body.name is not None:
        with svc._install_repo.session.no_autoflush:
            conflicts = await svc._has_install_conflict(
                server_url_hash=install.server_url_hash,
                name=install.name,
                template_id=install.template_id,
                exclude_id=install.id,
            )
        if conflicts:
            raise HTTPException(409, detail={"code": "install_already_exists"})

    saved = await svc._install_repo.update(install)
    await audit.record(
        event="mcp.install.patched",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
    )
    connector_id = await svc._connector_id_for_install(saved) or ""
    return _install_to_out(saved, connector_id=connector_id)


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


@router.post(
    "/installs/{connector_id}/grants/org",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_org_grant(
    connector_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_admin_oauth_token_manager)],
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
    connector_row = await MCPConnectorRepository(session, org_id=ctx.org_id).get(connector_id)
    if connector_row is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    await _reject_if_template_disabled(
        session, org_id=ctx.org_id, template_id=connector_row.template_id
    )
    try:
        grant = await svc.create_static_grant(
            connector_id=connector_id,
            grant_scope="org",
            plaintext=body.credential_plaintext,
            name=body.name,
        )
    except ValueError as exc:
        if str(exc) == "auth_method_not_supported_by_template":
            raise HTTPException(
                422, detail={"code": "auth_method_not_supported_by_template"}
            ) from exc
        raise HTTPException(400, detail={"code": "invalid_grant", "msg": str(exc)}) from exc
    await audit.record(
        event="mcp.grant.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"scope": "org"},
    )
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
    )
    await run_post_grant_discovery(
        connector_id=connector_id,
        workspace_id=None,
        actor_user_id=ctx.user.id,
        session=session,
        cred_service=cred_service,
        signer=signer,
        token_mgr=token_mgr,
    )
    return MCPCredentialGrantStatusOut(
        connector_id=grant.connector_id,
        grant_scope="org",
        workspace_id=None,
        user_id=None,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{connector_id}/grants/org",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_admin_org_grant(
    connector_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(connector_id=connector_id, grant_scope="org")
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"scope": "org"},
    )


@router.post(
    "/installs/{connector_id}/grants/org/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def admin_org_grant_oauth_start(
    connector_id: str,
    body: MCPOAuthStartIn,
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces an org-scope grant."""
    connector_row = await MCPConnectorRepository(session, org_id=ctx.org_id).get(connector_id)
    if connector_row is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    await _reject_if_template_disabled(
        session, org_id=ctx.org_id, template_id=connector_row.template_id
    )
    try:
        result = await svc.start_oauth_flow(
            connector_id=connector_id,
            actor_user_id=ctx.user.id,
            actor_org_id=ctx.org_id,
            grant_scope="org",
            workspace_id=None,
            user_id=None,
            frontend_origin=body.frontend_origin,
        )
    except OAuthStartError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return MCPOAuthStartOut(
        authorize_url=result.authorize_url,
        state=result.state,
        expires_at=result.expires_at,
    )


# ---------------------------------------------------------------------------
# Admin test-connection probe
# ---------------------------------------------------------------------------

_TEST_CONNECTION_TIMEOUT = 10.0


def _unwrap_exception(exc: Exception) -> BaseException:
    """Peel ExceptionGroup down to the first leaf so the error surfaced to the
    client names the real cause (an httpx / MCP protocol error), not the outer
    'unhandled errors in a TaskGroup (1 sub-exception)' wrapper the mcp client
    raises when its internal TaskGroup fails."""
    current: BaseException = exc
    while isinstance(current, BaseExceptionGroup) and current.exceptions:
        current = current.exceptions[0]
    return current


@router.post("/test-connection", response_model=TestConnectionOut)
async def admin_test_connection(
    body: TestConnectionIn,
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> TestConnectionOut:
    """Probe an MCP server URL without persisting anything.

    OAuth servers cannot be discovered anonymously — the MCP request is
    correctly rejected with 401. Instead we verify RFC 9728
    ``/.well-known/oauth-protected-resource`` is reachable and lists at least
    one authorization server. That confirms the server is alive and OAuth-
    capable; actual tool discovery has to wait until the OAuth flow completes.
    """
    if body.auth_method == "oauth":
        return await _probe_oauth_metadata(body.server_url)

    headers = dict(body.headers or {})
    if body.auth_method == "static" and body.credential_plaintext:
        headers.setdefault("Authorization", f"Bearer {body.credential_plaintext}")
    try:
        discovery = await asyncio.wait_for(
            load_mcp_tools_http(
                body.server_url,
                headers=headers or None,
                timeout=_TEST_CONNECTION_TIMEOUT,
                transport=body.transport,
            ),
            timeout=_TEST_CONNECTION_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        cause = _unwrap_exception(exc)
        message = str(cause) or repr(cause)
        return TestConnectionOut(
            ok=False,
            tool_count=0,
            error_code=type(cause).__name__,
            error_message=message[:256],
        )
    return TestConnectionOut(ok=True, tool_count=len(discovery.tools))


async def _probe_oauth_metadata(server_url: str) -> TestConnectionOut:
    """Probe RFC 9728 protected-resource metadata for an OAuth MCP server."""
    try:
        async with httpx.AsyncClient(timeout=_TEST_CONNECTION_TIMEOUT) as http:
            discovery = OAuthMetadataDiscovery(http)
            await asyncio.wait_for(
                discovery.fetch_protected_resource(server_url),
                timeout=_TEST_CONNECTION_TIMEOUT,
            )
    except Exception as exc:  # noqa: BLE001
        cause = _unwrap_exception(exc)
        message = str(cause) or repr(cause)
        return TestConnectionOut(
            ok=False,
            tool_count=0,
            error_code=type(cause).__name__,
            error_message=message[:256],
        )
    # Tool count is unknowable pre-authorization; report 0 and let the UI
    # phrase the success message for OAuth flows.
    return TestConnectionOut(ok=True, tool_count=0)
