"""Workspace MCP routes: template-centric catalog surface.

All routes live under ``/ws/{workspace_id}/mcp`` and operate on the
template-centric model — ``MCPConnectorTemplate`` / ``MCPConnector`` /
``MCPWorkspaceConnectorState`` / ``MCPCredentialGrant``.

Authorization recap (per spec §User Roles And Permissions):
- All routes require workspace membership (``require_member``).
- ``PUT .../templates/{id}/state``, ``POST .../templates``, and
  ``POST .../templates/{id}/promote`` require workspace admin
  (``require_admin``).
- ``*/grants/me*`` routes are open to any member.

Removed (replaced by template-centric surface):
  POST /installs, DELETE /installs/{id}, GET /available, GET /templates
  (old list), PATCH /connectors/{id}/state

Kept (unchanged):
  GET /connectors, GET /active-tools, all grant endpoints,
  /installs/{id}/refresh-discovery, /installs/{id}/tools/{tool}/invoke
"""

import asyncio
import time
from contextvars import ContextVar
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.middleware.rate_limit import limiter
from cubebox.api.routes.v1.admin_mcp import (
    _connector_to_facts,
    _install_to_out,
    _template_to_out,
)
from cubebox.api.schemas.mcp import (
    CreateGrantIn,
    McpActiveToolListOut,
    McpActiveToolOut,
    MCPConnectorOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPEffectiveConnectorListOut,
    MCPEffectiveConnectorOut,
    McpIconOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPWorkspaceConnectorStateOut,
    ToolInvokeOut,
    WsInstallInvokeIn,
    WsInstallRefreshIn,
)
from cubebox.api.schemas.mcp_catalog import (
    CreateTemplateIn,
    MCPTemplateOut,
    TemplateStateIn,
    WorkspaceCatalogListOut,
    WorkspaceCatalogRowOut,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user, require_admin, require_member
from cubebox.credentials.dependencies import get_credential_service
from cubebox.db.session import get_session
from cubebox.mcp.cubepi_runtime import _resolve_auth_from_spec
from cubebox.mcp.dependencies import (
    get_audit_sink,
    get_oauth_start_service,
    get_oauth_token_manager,
    get_user_token_signer,
    get_ws_effective_service,
    get_ws_grant_repo,
    get_ws_install_service,
)
from cubebox.mcp.effective import (
    MCPEffectiveConnectorDTO,
    MCPEffectiveConnectorService,
)
from cubebox.mcp.exceptions import MCPDiscoveryFailed
from cubebox.mcp.oauth import OAuthStartError, OAuthStartService
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models import User
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.services.credential import CredentialService
from cubebox.services.mcp_catalog import WorkspaceCatalogRow, build_workspace_catalog_rows
from cubebox.services.mcp_discovery import (
    _build_runtime_spec_for_discovery,
    discover_tools_for_install,
    run_post_grant_discovery,
)
from cubebox.services.mcp_installs import MCPConnectorService

router = APIRouter(prefix="/ws/{workspace_id}/mcp", tags=["workspace-mcp"])


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _template_to_connector_template_out(template: Any) -> MCPConnectorTemplateOut:
    """Map MCPConnectorTemplate → MCPConnectorTemplateOut for effective connector view."""
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
        install_summary=None,
    )


def _dto_to_effective_out(dto: MCPEffectiveConnectorDTO) -> MCPEffectiveConnectorOut:
    template_out: MCPConnectorTemplateOut | None = None
    if dto.template is not None:
        template_out = _template_to_connector_template_out(dto.template)
    state_out: MCPWorkspaceConnectorStateOut | None = None
    connector_id = ""
    if dto.workspace_state is not None:
        connector_id = dto.workspace_state.connector_id
        state_out = MCPWorkspaceConnectorStateOut(
            workspace_id=dto.workspace_state.workspace_id,
            connector_id=connector_id,
            enabled=dto.workspace_state.enabled,
            credential_policy=dto.workspace_state.credential_policy,  # type: ignore[arg-type]
            enablement_source=dto.workspace_state.enablement_source,
        )
    return MCPEffectiveConnectorOut(
        template=template_out,
        install=_install_to_out(dto.install, connector_id=connector_id),
        workspace_state=state_out,
        credential_policy=dto.credential_policy,
        required_grant_scope=dto.required_grant_scope,
        credential_availability=dto.credential_availability,
        credential_source=(dto.credential_source if dto.credential_source != "none" else None),
        credential_availability_by_scope=dto.credential_availability_by_scope,
        usable=dto.usable,
        reason=dto.reason,
    )


def _row_to_catalog_out(
    row: WorkspaceCatalogRow,
    *,
    dtos_by_connector_id: dict[str, MCPEffectiveConnectorDTO],
) -> WorkspaceCatalogRowOut:
    """Serialize a WorkspaceCatalogRow into WorkspaceCatalogRowOut.

    Enriches with effective state (usable/reason/credential_availability_by_scope)
    when a connector+state exists. Rows without a connector leave usable=None.
    """
    connector_facts = None
    usable: bool | None = None
    reason: str | None = None
    cred_avail: dict[Literal["org", "workspace", "user"], bool] = {
        "org": False,
        "workspace": False,
        "user": False,
    }

    if row.connector is not None:
        connector_facts = _connector_to_facts(row.connector)
        dto = dtos_by_connector_id.get(row.connector.id)
        if dto is not None:
            usable = dto.usable
            reason = dto.reason if not dto.usable else None
            cred_avail = dto.credential_availability_by_scope
        # No dto means the connector exists but no workspace state row yet
        # (connector was not yet enabled in this workspace): usable stays None

    return WorkspaceCatalogRowOut(
        template=_template_to_out(row.template),
        connector=connector_facts,
        enabled=row.enabled,
        usable=usable,
        reason=reason,
        credential_availability_by_scope=cred_avail,
    )


# ---------------------------------------------------------------------------
# Catalog handler
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=WorkspaceCatalogListOut)
async def ws_catalog(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
) -> WorkspaceCatalogListOut:
    """Workspace template catalog: every visible non-disabled template with effective state."""
    template_repo = MCPConnectorTemplateRepository(session)
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)

    templates = await template_repo.list_visible_for_workspace(ctx.org_id, workspace_id)
    connectors = await connector_repo.list_active()
    connectors_by_template: dict[str, Any] = {c.template_id: c for c in connectors}

    # States for this workspace keyed by connector_id
    states = await state_repo.list_for_workspace(workspace_id)
    states_by_connector: dict[str, Any] = {s.connector_id: s for s in states}

    disabled_ids = await settings_repo.disabled_template_ids()

    catalog_rows = build_workspace_catalog_rows(
        templates=templates,
        connectors_by_template_id=connectors_by_template,
        states_by_connector_id=states_by_connector,
        disabled_template_ids=disabled_ids,
    )

    # Enrich with effective state (usable/reason/credential_availability)
    # by fetching the effective DTO list once (include_unusable=True so
    # disabled-but-enabled rows surface with usable=False).
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id, ctx.user.id, include_unusable=True
    )
    dtos_by_connector_id: dict[str, MCPEffectiveConnectorDTO] = {
        dto.connector.id: dto for dto in dtos
    }

    items = [
        _row_to_catalog_out(row, dtos_by_connector_id=dtos_by_connector_id) for row in catalog_rows
    ]
    return WorkspaceCatalogListOut(items=items)


# ---------------------------------------------------------------------------
# Template state (lazy-enable / disable)
# ---------------------------------------------------------------------------


@router.put(
    "/templates/{template_id}/state",
    response_model=WorkspaceCatalogRowOut,
)
async def put_template_state(
    workspace_id: str,
    template_id: str,
    body: TemplateStateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> WorkspaceCatalogRowOut:
    """Lazy-enable (or disable) a template in this workspace.

    Route layer owns visibility + disabled rejections:
    - 404 template_not_visible: template not in list_visible_for_workspace
    - 409 template_disabled_in_org: org admin disabled this template
    - 200 WorkspaceCatalogRowOut on success
    """
    template_repo = MCPConnectorTemplateRepository(session)
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)

    # Visibility check
    visible = await template_repo.list_visible_for_workspace(ctx.org_id, workspace_id)
    template = next((t for t in visible if t.id == template_id), None)
    if template is None:
        raise HTTPException(404, detail={"code": "template_not_visible"})

    # Disabled check
    disabled_ids = await settings_repo.disabled_template_ids()
    if template_id in disabled_ids:
        raise HTTPException(409, detail={"code": "template_disabled_in_org"})

    # Delegate to service (lazy-ensure connector + upsert state row)
    await svc.set_workspace_enabled(
        template,
        workspace_id=workspace_id,
        enabled=body.enabled,
        credential_policy=body.credential_policy,
    )
    await audit.record(
        event="mcp.workspace_template.state_changed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={"workspace_id": workspace_id, "enabled": body.enabled},
    )

    # Build response row
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)

    connector = await connector_repo.get_by_template_id(template_id)
    connectors_by_template: dict[str, Any] = {template_id: connector} if connector else {}
    states: dict[str, Any] = {}
    if connector is not None:
        state = await state_repo.get_by_connector(workspace_id, connector.id)
        if state is not None:
            states[connector.id] = state

    rows = build_workspace_catalog_rows(
        templates=[template],
        connectors_by_template_id=connectors_by_template,
        states_by_connector_id=states,
        disabled_template_ids=set(),  # already checked above
    )

    dtos = await effective_svc.list_for_workspace_user(
        workspace_id, ctx.user.id, include_unusable=True
    )
    dtos_by_connector_id: dict[str, MCPEffectiveConnectorDTO] = {
        dto.connector.id: dto for dto in dtos
    }

    if rows:
        return _row_to_catalog_out(rows[0], dtos_by_connector_id=dtos_by_connector_id)

    # Fallback: template was excluded by disabled filter (shouldn't happen here
    # since we checked above, but be defensive)
    return WorkspaceCatalogRowOut(
        template=_template_to_out(template),
        connector=None,
        enabled=body.enabled,
        usable=None,
        reason=None,
        credential_availability_by_scope={"org": False, "workspace": False, "user": False},
    )


# ---------------------------------------------------------------------------
# Template CRUD (workspace-scoped)
# ---------------------------------------------------------------------------


@router.post(
    "/templates",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPTemplateOut,
)
async def create_workspace_template(
    workspace_id: str,
    body: CreateTemplateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPTemplateOut:
    """Create a workspace-scoped custom MCP template."""
    template_repo = MCPConnectorTemplateRepository(session)
    try:
        template = await template_repo.create_scoped(
            scope="workspace",
            org_id=ctx.org_id,
            workspace_id=workspace_id,
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
        details={"scope": "workspace", "workspace_id": workspace_id, "name": body.name},
    )
    return _template_to_out(template)


@router.post(
    "/templates/{template_id}/promote",
    response_model=MCPTemplateOut,
)
async def promote_workspace_template(
    workspace_id: str,
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPTemplateOut:
    """Promote a workspace-scoped template to org scope.

    Only the owning workspace (template.workspace_id == path workspace_id)
    may promote; otherwise 404.
    """
    template_repo = MCPConnectorTemplateRepository(session)
    template = await template_repo.get(template_id)
    if template is None or template.scope != "workspace" or template.workspace_id != workspace_id:
        raise HTTPException(404, detail={"code": "template_not_owned_by_workspace"})

    promoted = await template_repo.promote_to_org(template_id)
    await session.commit()
    await session.refresh(promoted)
    await audit.record(
        event="mcp.template.promoted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=template_id,
        details={"from_workspace_id": workspace_id},
    )
    return _template_to_out(promoted)


# ---------------------------------------------------------------------------
# Effective connector list (kept for chat surface + active-tools)
# ---------------------------------------------------------------------------


@router.get("/connectors", response_model=MCPEffectiveConnectorListOut)
async def list_workspace_connectors(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
) -> MCPEffectiveConnectorListOut:
    """Effective connector list for the workspace + current user.

    Workspace page lens: org installs disabled by the workspace admin (or
    never opted into) are hidden — the admin page is the surface for those.
    """
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id,
        ctx.user.id,
        include_unusable=True,
        include_disabled_org_installs=False,
    )
    return MCPEffectiveConnectorListOut(
        items=[_dto_to_effective_out(dto) for dto in dtos],
    )


@router.get("/active-tools", response_model=McpActiveToolListOut)
async def list_workspace_active_tools(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
) -> McpActiveToolListOut:
    """Flat list of active MCP tools + display metadata for the chat UI.

    For every usable install enabled in this workspace, enumerate the
    cached ``tools_cache`` and surface each tool with the namespaced name
    the runtime gives the LLM, the original (bare) tool name, the
    install's display name, and the server / per-tool icons captured at
    discovery time (MCP spec rev 2025-11-25 ``Implementation.icons`` +
    ``Tool.icons``).

    The frontend tool registry calls this once per workspace mount and
    keys lookups by ``namespaced_name`` to swap raw ``WebTools__web_search``
    style labels in tool-call cards for a server icon + bare name.

    Namespacing matches ``cubepi_runtime._build_namespaced_name_with_prefix``
    exactly — same slug, same collision/length suffix rules — so the
    name the LLM sees and the key the frontend uses agree.
    """
    from collections import Counter

    from cubebox.mcp.cubepi_runtime import (
        _NS_LENGTH_DEFENCE,
        _build_namespaced_name_with_prefix,
        _slugify_for_namespace,
    )

    specs = await effective_svc.list_runtime_specs(workspace_id, ctx.user.id)
    proposed_slugs: dict[str, str] = {
        spec.connector_id: _slugify_for_namespace(spec.name) for spec in specs
    }
    slug_counts: Counter[str] = Counter(proposed_slugs.values())

    def _icons_for(payload: list[dict[str, Any]] | None) -> list[McpIconOut]:
        # model_validate keeps optional cached_src from discovery materialisation.
        return [McpIconOut.model_validate(icon) for icon in (payload or [])]

    items: list[McpActiveToolOut] = []
    for spec in specs:
        slug = proposed_slugs[spec.connector_id]
        explicit_collision = slug_counts[slug] > 1
        risky_truncation = len(slug) > _NS_LENGTH_DEFENCE
        if explicit_collision or risky_truncation:
            safe = spec.connector_id.replace("-", "")
            suffix = f"_{safe[-4:] if len(safe) >= 4 else safe}"
        else:
            suffix = ""

        meta = spec.discovery_metadata or {}
        server_meta: dict[str, Any] = meta.get("server") or {}
        tool_icons_map: dict[str, list[dict[str, Any]]] = meta.get("tool_icons") or {}
        server_icons = _icons_for(server_meta.get("icons"))

        for tool in spec.tools_cache:
            bare = tool.get("name")
            if not bare:
                continue
            namespaced = _build_namespaced_name_with_prefix(slug, bare, suffix=suffix)
            items.append(
                McpActiveToolOut(
                    namespaced_name=namespaced,
                    bare_name=bare,
                    connector_id=spec.connector_id,
                    server_name=spec.name,
                    server_icons=server_icons,
                    tool_icons=_icons_for(tool_icons_map.get(bare)),
                )
            )

    return McpActiveToolListOut(items=items)


# ---------------------------------------------------------------------------
# Workspace refresh-discovery (connector-keyed, kept)
# ---------------------------------------------------------------------------


@router.post(
    "/installs/{connector_id}/refresh-discovery",
    response_model=MCPConnectorOut,
)
async def ws_refresh_discovery(
    workspace_id: str,
    connector_id: str,
    body: WsInstallRefreshIn,  # noqa: ARG001 — keep empty body for OpenAPI clarity
    session: Annotated[AsyncSession, Depends(get_session)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> MCPConnectorOut:
    """Re-discover tools for one install scoped to this workspace lens.

    The workspace path pins the credential policy lookup; no body
    argument needed (compare admin which needs ``workspace_id``).
    """
    install_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    try:
        await discover_tools_for_install(
            connector_id=connector_id,
            workspace_id=workspace_id,
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


# ---------------------------------------------------------------------------
# Grants (workspace + user scope) — all connector-keyed, kept unchanged
# ---------------------------------------------------------------------------


async def _reject_ws_grant_if_template_disabled(
    session: AsyncSession,
    *,
    org_id: str,
    connector_id: str,
) -> None:
    """Raise 409 template_disabled_in_org if the connector's template is org-disabled.

    Mirrors admin_mcp._reject_if_template_disabled but loads the connector first
    because ws grant routes are connector-keyed (not template-keyed).
    """
    connector_repo = MCPConnectorRepository(session, org_id=org_id)
    connector = await connector_repo.get(connector_id)
    if connector is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    if connector.template_id is None:
        return
    settings_repo = MCPTemplateSettingsRepository(session, org_id=org_id)
    disabled_ids = await settings_repo.disabled_template_ids()
    if connector.template_id in disabled_ids:
        raise HTTPException(409, detail={"code": "template_disabled_in_org"})


@router.post(
    "/installs/{connector_id}/grants/me",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_user_grant(
    workspace_id: str,
    connector_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPCredentialGrantStatusOut:
    await _reject_ws_grant_if_template_disabled(
        session, org_id=ctx.org_id, connector_id=connector_id
    )
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
            connector_id=connector_id,
            grant_scope="user",
            plaintext=body.credential_plaintext,
            workspace_id=workspace_id,
            user_id=ctx.user.id,
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
        details={"scope": "user", "workspace_id": workspace_id},
    )
    await run_post_grant_discovery(
        connector_id=connector_id,
        workspace_id=workspace_id,
        actor_user_id=ctx.user.id,
        session=session,
        cred_service=cred_service,
        signer=signer,
        token_mgr=token_mgr,
    )
    return MCPCredentialGrantStatusOut(
        connector_id=grant.connector_id,
        grant_scope="user",
        workspace_id=workspace_id,
        user_id=ctx.user.id,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{connector_id}/grants/me",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_my_user_grant(
    workspace_id: str,
    connector_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(
        connector_id=connector_id,
        grant_scope="user",
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"scope": "user", "workspace_id": workspace_id},
    )


@router.post(
    "/installs/{connector_id}/grants/me/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def my_user_grant_oauth_start(
    workspace_id: str,
    connector_id: str,
    body: MCPOAuthStartIn,
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a user-scope grant."""
    await _reject_ws_grant_if_template_disabled(
        session, org_id=ctx.org_id, connector_id=connector_id
    )
    try:
        result = await svc.start_oauth_flow(
            connector_id=connector_id,
            actor_user_id=ctx.user.id,
            actor_org_id=ctx.org_id,
            grant_scope="user",
            workspace_id=workspace_id,
            user_id=ctx.user.id,
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


@router.post(
    "/installs/{connector_id}/grants/workspace",
    response_model=MCPCredentialGrantStatusOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_grant(
    workspace_id: str,
    connector_id: str,
    body: CreateGrantIn,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPCredentialGrantStatusOut:
    await _reject_ws_grant_if_template_disabled(
        session, org_id=ctx.org_id, connector_id=connector_id
    )
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
            connector_id=connector_id,
            grant_scope="workspace",
            plaintext=body.credential_plaintext,
            workspace_id=workspace_id,
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
        details={"scope": "workspace", "workspace_id": workspace_id},
    )
    await run_post_grant_discovery(
        connector_id=connector_id,
        workspace_id=workspace_id,
        actor_user_id=ctx.user.id,
        session=session,
        cred_service=cred_service,
        signer=signer,
        token_mgr=token_mgr,
    )
    return MCPCredentialGrantStatusOut(
        connector_id=grant.connector_id,
        grant_scope="workspace",
        workspace_id=workspace_id,
        user_id=None,
        grant_status=grant.grant_status,
        has_value=True,
        expires_at=grant.expires_at,
    )


@router.delete(
    "/installs/{connector_id}/grants/workspace",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_grant(
    workspace_id: str,
    connector_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    await svc.disconnect_grant(
        connector_id=connector_id,
        grant_scope="workspace",
        workspace_id=workspace_id,
    )
    await audit.record(
        event="mcp.grant.deleted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"scope": "workspace", "workspace_id": workspace_id},
    )


@router.post(
    "/installs/{connector_id}/grants/workspace/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def workspace_grant_oauth_start(
    workspace_id: str,
    connector_id: str,
    body: MCPOAuthStartIn,
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a workspace-scope grant."""
    await _reject_ws_grant_if_template_disabled(
        session, org_id=ctx.org_id, connector_id=connector_id
    )
    try:
        result = await svc.start_oauth_flow(
            connector_id=connector_id,
            actor_user_id=ctx.user.id,
            actor_org_id=ctx.org_id,
            grant_scope="workspace",
            workspace_id=workspace_id,
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
# Try It (workspace surface).
# ---------------------------------------------------------------------------


_INVOKE_USER_ID: ContextVar[str | None] = ContextVar("_INVOKE_USER_ID", default=None)
_INVOKE_TIMEOUT_SECONDS = 10.0


def _set_invoke_user_id(user: User = Depends(current_active_user)) -> User:
    _INVOKE_USER_ID.set(user.id)
    return user


def _invoke_rate_key(_req: Request | None = None) -> str:
    return _INVOKE_USER_ID.get() or "anonymous"


async def _invoke_tool_via_cubepi(
    server_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None,
    timeout: float,
    transport: str,
) -> Any:
    """Thin wrapper for unit-test monkeypatching."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client

    if transport == "streamable_http":
        timeout_td = timedelta(seconds=timeout)
        async with streamablehttp_client(
            server_url,
            headers=headers,
            timeout=timeout_td,
            sse_read_timeout=timeout_td,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    elif transport == "sse":
        async with sse_client(
            server_url,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=timeout,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    else:
        raise ValueError(f"unsupported transport: {transport!r}")

    out: dict[str, Any] = {"isError": bool(getattr(result, "isError", False))}
    content_list: list[dict[str, Any]] = []
    for c in getattr(result, "content", []) or []:
        ctype = getattr(c, "type", None)
        if ctype == "text":
            content_list.append({"type": "text", "text": getattr(c, "text", "")})
        elif ctype == "image":
            content_list.append(
                {
                    "type": "image",
                    "data": getattr(c, "data", ""),
                    "mimeType": getattr(c, "mimeType", ""),
                }
            )
    out["content"] = content_list
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        out["structuredContent"] = structured
    return out


@router.post(
    # `tool_name:path` captures slash-containing names (some MCP
    # servers expose tools like `repos/list`). See admin_mcp.py
    # equivalent for the rationale.
    "/installs/{connector_id}/tools/{tool_name:path}/invoke",
    response_model=ToolInvokeOut,
)
@limiter.limit("30/minute", key_func=_invoke_rate_key)
async def ws_invoke_tool(
    request: Request,  # noqa: ARG001
    workspace_id: str,
    connector_id: str,
    tool_name: str,
    body: WsInstallInvokeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_ws_grant_repo)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    _rate_key_user: Annotated[User, Depends(_set_invoke_user_id)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> ToolInvokeOut:
    """Invoke a single tool on an installed connector (workspace surface)."""
    install_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id, ctx.user.id, include_unusable=True
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
    spec = _build_runtime_spec_for_discovery(install=install, grant=dto.grant)
    started = time.perf_counter()
    try:
        resolved = await _resolve_auth_from_spec(
            spec=spec,
            workspace_id=workspace_id,
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
                "workspace_id": workspace_id,
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
            timeout=_INVOKE_TIMEOUT_SECONDS,
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
                "workspace_id": workspace_id,
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
            "workspace_id": workspace_id,
            "ok": True,
        },
    )
    return ToolInvokeOut(ok=True, result=result, duration_ms=duration)
