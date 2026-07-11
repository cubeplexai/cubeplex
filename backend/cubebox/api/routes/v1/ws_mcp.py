"""Workspace MCP routes: four-layer connector surface.

All routes live under ``/ws/{workspace_id}/mcp`` and operate on the
four-layer model — ``MCPConnectorTemplate`` / ``MCPConnector`` /
``MCPWorkspaceConnectorState`` / ``MCPCredentialGrant``.

Authorization recap (per spec §User Roles And Permissions):
- All routes require workspace membership (``require_member``).
- ``POST /installs``, ``DELETE /installs/{id}``, ``PATCH /connectors/{id}/state``,
  and the workspace-scope grant routes require workspace admin
  (``require_admin``).
- ``*/grants/me*`` routes are open to any member.
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
    _install_to_out,
    _template_to_out,
    _validate_install_policy_pairing,
)
from cubebox.api.schemas.mcp import (
    CreateGrantIn,
    McpActiveToolListOut,
    McpActiveToolOut,
    MCPConnectorOut,
    MCPConnectorTemplateListOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPEffectiveConnectorListOut,
    MCPEffectiveConnectorOut,
    McpIconOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPWorkspaceConnectorStateOut,
    PatchWorkspaceStateIn,
    ToolInvokeOut,
    WorkspaceCreateInstallIn,
    WsInstallInvokeIn,
    WsInstallRefreshIn,
)
from cubebox.api.schemas.mcp_ws_available import (
    WsAvailableListOut,
    WsAvailableOut,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user, require_admin, require_member
from cubebox.credentials.dependencies import get_credential_service
from cubebox.db.session import get_session
from cubebox.mcp.cubepi_runtime import _resolve_auth_from_spec
from cubebox.mcp.dependencies import (
    get_audit_sink,
    get_connector_template_service,
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
    MCPCredentialGrantRepository,
)
from cubebox.services.credential import CredentialService
from cubebox.services.mcp_discovery import (
    _build_runtime_spec_for_discovery,
    discover_tools_for_install,
    run_post_grant_discovery,
)
from cubebox.services.mcp_installs import MCPConnectorService
from cubebox.services.mcp_templates import MCPConnectorTemplateService

router = APIRouter(prefix="/ws/{workspace_id}/mcp", tags=["workspace-mcp"])


def _dto_to_effective_out(dto: MCPEffectiveConnectorDTO) -> MCPEffectiveConnectorOut:
    template_out: MCPConnectorTemplateOut | None = None
    if dto.template is not None:
        template_out = _template_to_out(dto.template)
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


@router.get("/available", response_model=WsAvailableListOut)
async def list_workspace_available(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    install_svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    template_svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
) -> WsAvailableListOut:
    """Connectors the workspace can opt into.

    Includes org installs not yet enabled in this workspace + templates
    the workspace doesn't already have. Spec §3.2.
    """
    from cubebox.services.mcp_ws_available import compute_available_rows

    org_installs = await install_svc._install_repo.list_org_installs()
    ws_installs = await install_svc._install_repo.list_workspace_installs(workspace_id)
    ws_states = await install_svc._state_repo.list_for_workspace(workspace_id)
    templates = await template_svc.list_active()

    rows = compute_available_rows(
        ws_id=workspace_id,
        org_installs=org_installs,
        ws_installs=ws_installs,
        ws_states=ws_states,
        templates=templates,
    )

    installs_by_id = {i.id: i for i in org_installs}
    templates_by_id = {t.id: t for t in templates}

    connector_ids: dict[str, str] = {}
    for inst in org_installs:
        cid = await install_svc._connector_id_for_install(inst)
        if cid is not None:
            connector_ids[inst.id] = cid

    items: list[WsAvailableOut] = []
    for row in rows:
        credential_availability_by_scope: dict[Literal["org", "workspace", "user"], bool] = {
            "org": False,
            "workspace": False,
            "user": False,
        }
        if row.source == "org_install" and row.connector_id is not None:
            connector_id = connector_ids.get(row.connector_id, row.connector_id)
            org_grant = await install_svc._grant_repo.get_for_connector_scope(
                connector_id=connector_id,
                grant_scope="org",
                workspace_id=None,
                user_id=None,
            )
            workspace_grant = await install_svc._grant_repo.get_for_connector_scope(
                connector_id=connector_id,
                grant_scope="workspace",
                workspace_id=workspace_id,
                user_id=None,
            )
            user_grant = await install_svc._grant_repo.get_for_connector_scope(
                connector_id=connector_id,
                grant_scope="user",
                workspace_id=workspace_id,
                user_id=ctx.user.id,
            )
            credential_availability_by_scope = {
                "org": org_grant is not None,
                "workspace": workspace_grant is not None,
                "user": user_grant is not None,
            }
        install_out = (
            _install_to_out(
                installs_by_id[row.connector_id],
                connector_id=connector_ids.get(row.connector_id, ""),
            )
            if row.source == "org_install" and row.connector_id is not None
            else None
        )
        template_out = (
            _template_to_out(templates_by_id[row.template_id])
            if row.template_id is not None and row.template_id in templates_by_id
            else None
        )
        items.append(
            WsAvailableOut(
                source=row.source,
                install=install_out,
                template=template_out,
                reason=row.reason,
                credential_availability_by_scope=credential_availability_by_scope,
            )
        )
    return WsAvailableListOut(items=items)


@router.post(
    "/installs",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConnectorOut,
)
async def create_workspace_install(
    workspace_id: str,
    body: WorkspaceCreateInstallIn,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    template_svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorOut:
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
                    "msg": "template_id is required for workspace installs",
                    "input": None,
                }
            ],
        )
    try:
        template = await template_svc.get_active(body.template_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "connector_template_not_found"}) from exc

    try:
        result = await svc.create_from_template_for_workspace(
            template=template,
            workspace_id=workspace_id,
            auth_method=body.auth_method,
            credential_policy=body.default_credential_policy,
        )
    except ValueError as exc:
        # Service-side guards raise ValueError with a canonical code as
        # the message (``auth_method_not_supported_by_template``,
        # ``install_already_exists``). 409 for the uniqueness rule,
        # 400 for everything else.
        code = str(exc)
        status_code = 409 if code == "install_already_exists" else 400
        raise HTTPException(status_code, detail={"code": code}) from exc

    await audit.record(
        event="mcp.install.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=result.install.id,
        details={"scope": "workspace", "workspace_id": workspace_id},
    )
    return _install_to_out(result.install, connector_id=result.connector_id)


@router.delete(
    "/installs/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_install(
    workspace_id: str,
    connector_id: str,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> None:
    install = await svc._install_repo.get(connector_id)
    state = await svc._state_repo.get(workspace_id, connector_id)
    if install is None or state is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    try:
        await svc.uninstall(connector_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"}) from exc
    await audit.record(
        event="mcp.install.uninstalled",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"workspace_id": workspace_id},
    )


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


@router.patch(
    "/connectors/{connector_id}/state",
    response_model=MCPWorkspaceConnectorStateOut,
)
async def patch_workspace_connector_state(
    workspace_id: str,
    connector_id: str,
    body: PatchWorkspaceStateIn,
    svc: Annotated[MCPConnectorService, Depends(get_ws_install_service)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPWorkspaceConnectorStateOut:
    """Workspace-admin-only enablement / credential-policy edit.

    The path lives under ``/connectors`` (not ``/installs``) by design —
    per spec, install-lifecycle operations stay under ``/installs`` while
    per-workspace state edits sit under the effective-connector view.
    """
    install = await svc._install_repo.get(connector_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})

    current = await svc._state_repo.get(workspace_id, connector_id)
    if current is None:
        # No state row exists. For org-scope installs in the caller's org this
        # is the normal shape when ``auto_enable.mode`` was ``none`` or only
        # distributed to other workspaces — the admin UI surfaces the install
        # as "disabled" and this PATCH is the path to selectively enable it.
        # Upsert in that case using the body (or the install's default policy
        # when ``credential_policy`` is omitted), with
        # ``enablement_source='workspace_manual'``. For workspace-scope installs
        # a missing state row is an internal inconsistency (the install-create
        # flow writes it) — keep 404 there.
        if install.install_scope != "org":
            raise HTTPException(404, detail={"code": "mcp_workspace_state_not_found"})
        # Repository scoping already guarantees this, but assert explicitly so
        # a future refactor can't quietly cross orgs.
        assert install.org_id == ctx.org_id, "install.org_id must match request context org"

        new_policy = (
            body.credential_policy
            if body.credential_policy is not None
            else install.default_credential_policy
        )
        _validate_install_policy_pairing(
            install=install,
            requested_policy=new_policy,
            field="credential_policy",
        )
        new_enabled = body.enabled if body.enabled is not None else True

        connector_id = install.id
        saved = await svc._state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            connector_id=connector_id,
            enabled=new_enabled,
            credential_policy=new_policy,
            enablement_source="workspace_manual",
            updated_by_user_id=ctx.user.id,
        )
        await audit.record(
            event="mcp.workspace_state.patched",
            actor_user_id=ctx.user.id,
            org_id=ctx.org_id,
            target_id=connector_id,
            details={"workspace_id": workspace_id, "created": True},
        )
        return MCPWorkspaceConnectorStateOut(
            workspace_id=saved.workspace_id,
            connector_id=saved.connector_id,
            enabled=saved.enabled,
            credential_policy=saved.credential_policy,  # type: ignore[arg-type]
            enablement_source=saved.enablement_source,
        )

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

    saved = await svc._state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=current.connector_id,
        enabled=new_enabled,
        credential_policy=new_policy,
        enablement_source=current.enablement_source,
        updated_by_user_id=ctx.user.id,
    )
    await audit.record(
        event="mcp.workspace_state.patched",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=connector_id,
        details={"workspace_id": workspace_id},
    )
    return MCPWorkspaceConnectorStateOut(
        workspace_id=saved.workspace_id,
        connector_id=saved.connector_id,
        enabled=saved.enabled,
        credential_policy=saved.credential_policy,  # type: ignore[arg-type]
        enablement_source=saved.enablement_source,
    )


# ---------------- workspace + user grants ---------------- #


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
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a user-scope grant."""
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
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces a workspace-scope grant."""
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
