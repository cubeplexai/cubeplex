"""Admin MCP routes: four-layer connector surface.

Routes under ``/admin/mcp/{templates,installs,...}`` operate on the
four-layer model — ``MCPConnectorTemplate`` / ``MCPConnectorInstall`` /
``MCPCredentialGrant``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from cubebox.api.schemas.mcp import (
    AdminCreateInstallIn,
    CreateGrantIn,
    MCPAdminInstallEffectiveOut,
    MCPConnectorInstallListOut,
    MCPConnectorInstallOut,
    MCPConnectorTemplateListOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPToolEntry,
    PatchInstallIn,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user
from cubebox.mcp.dependencies import (
    get_admin_install_service,
    get_admin_request_context,
    get_audit_sink,
    get_connector_template_service,
    get_grant_repo,
    get_oauth_start_service,
)
from cubebox.mcp.oauth import OAuthStartError, OAuthStartService
from cubebox.models import MCPConnectorInstall, User
from cubebox.models.mcp import MCPCredentialGrant
from cubebox.repositories.mcp import MCPCredentialGrantRepository
from cubebox.services.mcp_installs import MCPConnectorInstallService
from cubebox.services.mcp_templates import MCPConnectorTemplateService

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])

# A separate router for the public template list — authenticated but not
# org-admin gated. Mounted at /api/v1 in api/app.py.
public_templates_router = APIRouter(prefix="/mcp", tags=["mcp-public"])


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


def _install_to_out(
    install: MCPConnectorInstall,
    *,
    include_tool_citations: bool = False,
) -> MCPConnectorInstallOut:
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
        tool_count=len(tool_entries),
        tools=tool_entries,
        tool_citations=(install.tool_citations or {}) if include_tool_citations else None,
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
        items=[_install_to_out(install, include_tool_citations=True) for install in org_rows],
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
                    "msg": "template_id is required for org installs",
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
        # Service-side guards raise ValueError with a canonical code as the
        # message (e.g. ``auth_method_not_supported_by_template``,
        # ``workspace_not_in_org``, ``unknown distribution mode: ...``). Map
        # to 400 with the message as ``code`` so the frontend can parse
        # uniformly across admin and workspace install routes.
        raise HTTPException(400, detail={"code": str(exc)}) from exc

    await audit.record(
        event="mcp.install.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install.id,
        details={"scope": "org", "template_id": template.id},
    )
    return _install_to_out(install, include_tool_citations=True)


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
    return _install_to_out(install, include_tool_citations=True)


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
    return _install_to_out(saved, include_tool_citations=True)


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
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPOAuthStartOut:
    """Start an OAuth flow that produces an org-scope grant."""
    try:
        result = await svc.start_oauth_flow(
            install_id=install_id,
            actor_user_id=ctx.user.id,
            actor_org_id=ctx.org_id,
            grant_scope="org",
            workspace_id=None,
            user_id=None,
        )
    except OAuthStartError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    return MCPOAuthStartOut(
        authorize_url=result.authorize_url,
        state=result.state,
        expires_at=result.expires_at,
    )


# ---------------------------------------------------------------------------
# Admin install effective (org-row reason derivation — spec §4 admin row).
# ---------------------------------------------------------------------------


def _derive_admin_org_effective(
    install: MCPConnectorInstall,
    org_grant: MCPCredentialGrant | None,
) -> MCPAdminInstallEffectiveOut:
    """Spec §4 admin row: ordered decision table.

    Rule order (first match wins):
      1. ``install.auth_method == 'none'`` → usable.
      2. org grant exists, ``grant_status == 'valid'`` → usable.
      3. org grant exists, ``grant_status == 'expired'``, no refresh
         available → grant_expired.
      4. no org grant, ``install.auth_method == 'oauth'``,
         ``install.auth_status == 'pending'`` → pending_oauth.
      5. no org grant otherwise → missing_org_grant.
    """
    if install.auth_method == "none":
        return MCPAdminInstallEffectiveOut(install_id=install.id, usable=True, reason="usable")
    # Rule 2: usable when the org grant is valid OR when it's expired
    # but still refreshable — the runtime token manager rotates the
    # access token on next call. Matches the workspace-side
    # effective service (compute_effective_state rule 8 only emits
    # `grant_expired` when the grant is expired AND there is no
    # refresh credential).
    if org_grant is not None and (
        org_grant.grant_status == "valid"
        or (org_grant.grant_status == "expired" and org_grant.refresh_credential_id is not None)
    ):
        return MCPAdminInstallEffectiveOut(install_id=install.id, usable=True, reason="usable")
    if (
        org_grant is not None
        and org_grant.grant_status == "expired"
        and org_grant.refresh_credential_id is None
    ):
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="grant_expired"
        )
    if org_grant is None and install.auth_method == "oauth" and install.auth_status == "pending":
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="pending_oauth"
        )
    return MCPAdminInstallEffectiveOut(
        install_id=install.id, usable=False, reason="missing_org_grant"
    )


@router.get(
    "/installs/{install_id}/effective",
    response_model=MCPAdminInstallEffectiveOut,
)
async def get_admin_install_effective(
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPAdminInstallEffectiveOut:
    """Org-row effective state for the admin page (bypasses workspace lens)."""
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    if install.install_scope != "org":
        # Workspace-scope installs get their effective state from the
        # workspace lens — this endpoint is org-row only.
        raise HTTPException(400, detail={"code": "not_an_org_install"})
    org_grant = await grant_repo.get_org_grant(install_id)
    return _derive_admin_org_effective(install, org_grant)


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
