"""Admin MCP routes: four-layer connector surface.

Routes under ``/admin/mcp/{templates,installs,...}`` operate on the
four-layer model — ``MCPConnectorTemplate`` / ``MCPConnectorInstall`` /
``MCPCredentialGrant``.
"""

import asyncio
import time
from contextvars import ContextVar
from typing import Annotated, Any

from cubepi.mcp import load_mcp_tools_http
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.middleware.rate_limit import limiter
from cubebox.api.schemas.mcp import (
    AdminCreateInstallIn,
    AdminInstallInvokeIn,
    AdminInstallRefreshIn,
    CreateGrantIn,
    MCPAdminInstallEffectiveOut,
    MCPConnectorInstallOut,
    MCPConnectorTemplateListOut,
    MCPConnectorTemplateOut,
    MCPCredentialGrantStatusOut,
    MCPOAuthStartIn,
    MCPOAuthStartOut,
    MCPToolEntry,
    PatchInstallIn,
    PromoteInstallIn,
    TestConnectionIn,
    TestConnectionOut,
    ToolCitationUpsertIn,
    ToolInvokeOut,
)
from cubebox.api.schemas.mcp_admin_connector import (
    AdminOrgConnectorListOut,
    AdminOrgConnectorOut,
)
from cubebox.audit.sink import AuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user
from cubebox.credentials.dependencies import build_credential_service, get_encryption_backend
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.mcp.dependencies import (
    get_admin_install_service,
    get_admin_oauth_token_manager,
    get_admin_request_context,
    get_audit_sink,
    get_connector_template_service,
    get_grant_repo,
    get_oauth_start_service,
    get_user_token_signer,
)
from cubebox.mcp.exceptions import MCPDiscoveryFailed
from cubebox.mcp.oauth import OAuthStartError, OAuthStartService
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.models import MCPConnectorInstall, User
from cubebox.models.mcp import MCPCredentialGrant
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.repositories.workspace import WorkspaceRepository
from cubebox.services.mcp_admin_connectors import (
    build_workspace_distribution,
    derive_admin_org_effective,
)
from cubebox.services.mcp_discovery import (
    discover_tools_for_install,
    run_post_grant_discovery,
)
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


def _install_to_out(install: MCPConnectorInstall) -> MCPConnectorInstallOut:
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
        tool_citations=dict(install.tool_citations or {}),
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
    _validate_pair(install.auth_method, requested_policy, field=field)


def _validate_pair(auth_method: str, policy: str, *, field: str) -> None:
    """Raise the canonical 422 when (auth_method, policy) pairing is invalid.

    Single source of truth for the rule reused at install-create,
    install-patch, and auth-method-switch sites.
    """
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


@router.get("/templates", response_model=MCPConnectorTemplateListOut)
async def list_admin_templates(
    svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorTemplateListOut:
    """Admin view over the global connector template catalog."""
    templates = await svc.list_active()
    return MCPConnectorTemplateListOut(items=[_template_to_out(t) for t in templates])


@router.post(
    "/installs",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConnectorInstallOut,
)
async def create_admin_install(
    body: AdminCreateInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    template_svc: Annotated[MCPConnectorTemplateService, Depends(get_connector_template_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_admin_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    """Create an org-scope install, optionally fanning out into workspaces.

    Two branches:

    * ``template_id`` provided → install from a catalog template.
    * ``template_id is None`` → custom install (admin-supplied URL +
      transport + name). The schema validator enforces the required
      custom-install fields.
    """
    install: MCPConnectorInstall
    template_id_for_audit: str | None = None
    if body.template_id is None:
        # Custom install. The schema validator already guaranteed
        # name/server_url/transport are present.
        assert body.name is not None
        assert body.server_url is not None
        assert body.transport is not None
        try:
            install = await svc.create_custom_install_for_org(
                name=body.name,
                server_url=body.server_url,
                transport=body.transport,
                auth_method=body.auth_method,
                default_credential_policy=body.default_credential_policy,
                headers=body.headers,
                distribution=body.auto_enable.model_dump(),
            )
        except ValueError as exc:
            code = str(exc)
            status_code = 409 if code == "install_already_exists" else 400
            raise HTTPException(status_code, detail={"code": code}) from exc
    else:
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
            # ``workspace_not_in_org``, ``unknown distribution mode: ...``,
            # ``install_already_exists``). 409 for the uniqueness rule,
            # 400 for everything else.
            code = str(exc)
            status_code = 409 if code == "install_already_exists" else 400
            raise HTTPException(status_code, detail={"code": code}) from exc
        template_id_for_audit = template.id

    # Org-policy static one-shot grant: when admin passes
    # ``credential_plaintext`` the install is born with a usable grant.
    # Schema validator already enforces auth_method='static' and
    # default_credential_policy='org'.
    if body.credential_plaintext is not None:
        try:
            await svc.create_static_grant(
                install_id=install.id,
                grant_scope="org",
                plaintext=body.credential_plaintext,
            )
        except ValueError as exc:
            raise HTTPException(400, detail={"code": str(exc)}) from exc
        # Static one-shot grant on install: validate by discovering
        # tools right away. Failures land in install.discovery_status
        # / last_error rather than failing the install create.
        cred_service = build_credential_service(
            session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
        )
        await run_post_grant_discovery(
            install_id=install.id,
            workspace_id=None,
            actor_user_id=ctx.user.id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,
        )

    await audit.record(
        event="mcp.install.created",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install.id,
        details={"scope": "org", "template_id": template_id_for_audit},
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


@router.post(
    "/installs/{install_id}/refresh-discovery",
    response_model=MCPConnectorInstallOut,
)
async def admin_refresh_discovery(
    install_id: str,
    body: AdminInstallRefreshIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_admin_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorInstallOut:
    """Re-discover tools for one install and persist into ``tools_cache``.

    Requires ``workspace_id`` when the install's default credential
    policy is workspace/user-scoped — the grant lookup needs the
    workspace lens. Org-policy installs are looked up against the org
    grant directly.
    """
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
    )
    install_repo = MCPConnectorInstallRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    # Determine effective policy for the workspace_id requirement check.
    # If body.workspace_id is provided, the workspace's state row's
    # credential_policy (when set) OVERRIDES install.default_credential_policy.
    # That lookup runs first so workspace overrides aren't ignored.
    # If body.workspace_id is missing, fall back to the install default —
    # so an admin refreshing an org-default install with no workspace
    # lens uses the org grant. Admins who want to refresh as a specific
    # workspace's effective policy must pass `workspace_id`; otherwise
    # they get the install-default-scope view.
    effective_policy = install.default_credential_policy
    if body.workspace_id:
        from cubebox.repositories.mcp import (
            MCPWorkspaceConnectorStateRepository,
        )

        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
        ws_state = await state_repo.get(body.workspace_id, install_id)
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
            install_id=install_id,
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
    refreshed = await install_repo.get(install_id)
    assert refreshed is not None
    return _install_to_out(refreshed)


@router.post(
    "/installs/{install_id}/promote-to-org",
    response_model=MCPConnectorInstallOut,
)
async def admin_promote_install_to_org(
    install_id: str,
    body: PromoteInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    """Promote a workspace-scope install to org scope.

    The source workspace's existing state row is preserved — it is
    explicitly excluded from the fan-out so the workspace's
    pre-promote credential policy doesn't get clobbered.
    ``auto_enroll_new_workspaces`` is set per ``distribution.mode``:
    ``True`` for ``mode='all'``, ``False`` otherwise.
    """
    try:
        install = await svc.promote_workspace_install_to_org(
            install_id=install_id,
            distribution=body.distribution.model_dump(),
        )
    except ValueError as exc:
        code = str(exc)
        if code == "connector_install_not_found":
            status_code = 404
        elif code in {"install_already_org_scope", "install_already_exists"}:
            status_code = 409
        else:
            status_code = 400
        raise HTTPException(status_code, detail={"code": code}) from exc
    await audit.record(
        event="mcp.install.promoted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"distribution_mode": body.distribution.mode},
    )
    return _install_to_out(install)


@router.put(
    "/installs/{install_id}/tool-citations",
    response_model=MCPConnectorInstallOut,
)
async def admin_upsert_tool_citation(
    install_id: str,
    body: ToolCitationUpsertIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorInstallOut:
    """Upsert or clear one tool's citation mapping on an install.

    ``config=None`` clears the entry; a dict upserts it.
    """
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    current = dict(install.tool_citations or {})
    if body.config is None:
        current.pop(body.tool_name, None)
    else:
        current[body.tool_name] = body.config
    install.tool_citations = current
    saved = await svc._install_repo.update(install)
    return _install_to_out(saved)


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
    # `tool_name:path` captures slash-containing names (some MCP
    # servers expose tools like `repos/list`). FastAPI's default
    # string parameter stops at the next slash and decodes %2F as
    # one too, so frontend encodeURIComponent doesn't save us.
    "/installs/{install_id}/tools/{tool_name:path}/invoke",
    response_model=ToolInvokeOut,
)
@limiter.limit("30/minute", key_func=_admin_invoke_rate_key)
async def admin_invoke_tool(
    request: Request,  # noqa: ARG001
    install_id: str,
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
    from cubebox.api.routes.v1.ws_mcp import _invoke_tool_via_cubepi
    from cubebox.mcp.cubepi_runtime import _resolve_auth_from_spec
    from cubebox.mcp.effective import MCPEffectiveConnectorService
    from cubebox.repositories.mcp import (
        MCPConnectorTemplateRepository,
        MCPWorkspaceConnectorStateRepository,
    )
    from cubebox.services.mcp_discovery import _build_runtime_spec_for_discovery

    install_repo = MCPConnectorInstallRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    # Same effective-policy logic as refresh-discovery: workspace_state
    # credential_policy overrides install default when present.
    effective_policy = install.default_credential_policy
    if body.workspace_id:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
        ws_state = await state_repo.get(body.workspace_id, install_id)
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
            install_repo=install_repo,
            state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
            grant_repo=grant_repo,
            org_id=ctx.org_id,
        )
        dtos = await effective_svc.list_for_workspace_user(
            body.workspace_id, ctx.user.id, include_unusable=True
        )
        dto = next((d for d in dtos if d.install.id == install_id), None)
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
        grant = await grant_repo.get_org_grant(install_id)
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
            target_id=install_id,
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
            target_id=install_id,
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
        target_id=install_id,
        details={
            "tool_name": tool_name,
            "workspace_id": body.workspace_id,
            "ok": True,
        },
    )
    return ToolInvokeOut(ok=True, result=result, duration_ms=duration)


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

    # Switching auth_method post-install is supported so the credential
    # provisioning step (not the install step) is where the operator picks
    # OAuth vs static token on multi-method templates. Guard rails:
    # - the new method must be in the template's supported_auth_methods
    #   (free for custom installs, which have no template)
    # - reject when any credential grant already exists — those grants
    #   are tied to the old method and would orphan; require the admin
    #   to disconnect first
    # - validate the (auth_method, policy) pairing using the effective
    #   values after the patch applies
    new_auth_method = body.auth_method if body.auth_method is not None else install.auth_method
    new_policy = (
        body.default_credential_policy
        if body.default_credential_policy is not None
        else install.default_credential_policy
    )
    if body.auth_method is not None and body.auth_method != install.auth_method:
        if install.template_id is not None:
            template_repo = MCPConnectorTemplateRepository(svc._install_repo.session)
            template = await template_repo.get(install.template_id)
            if template is not None and body.auth_method not in (
                template.supported_auth_methods or []
            ):
                raise _policy_field_error(
                    "auth_method",
                    "auth_method_not_supported_by_template",
                )
        grant_repo = MCPCredentialGrantRepository(svc._install_repo.session, org_id=install.org_id)
        if await grant_repo.has_any_grant(install_id):
            raise HTTPException(
                409,
                detail={"code": "auth_method_change_blocked_by_existing_grant"},
            )
        # Clear OAuth-only side state when leaving OAuth — DCR results
        # for the old client identity must not leak into a static or
        # no-auth install. Symmetric: nothing to clear when leaving
        # static (the secret lives on the grant, which we already
        # required to be absent).
        if install.auth_method == "oauth" and body.auth_method != "oauth":
            install.oauth_client_config = {}
        install.auth_method = body.auth_method
        install.auth_status = "not_required" if body.auth_method == "none" else "pending"

    _validate_pair(new_auth_method, new_policy, field="default_credential_policy")
    if body.default_credential_policy is not None:
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

    # Preflight the org-wide R1/R2/R3 uniqueness rule. PATCH on name /
    # server_url could otherwise hit `IntegrityError` from
    # `uq_mcp_connector_install_{name,url,template}_per_org` and surface
    # as a 500. Excluding the row itself keeps no-op renames and
    # unrelated patches (e.g. just headers) from tripping on the
    # install's own values. ``no_autoflush`` prevents SQLAlchemy from
    # flushing the dirty install row before the SELECT runs — without
    # it, the autoflush itself raises the IntegrityError we're trying
    # to translate.
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
    # Trigger discovery so the operator immediately sees whether the
    # token actually works against the server, rather than a silent
    # "saved" with discovery_status still 'not_run'.
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id
    )
    await run_post_grant_discovery(
        install_id=install_id,
        workspace_id=None,
        actor_user_id=ctx.user.id,
        session=session,
        cred_service=cred_service,
        signer=signer,
        token_mgr=token_mgr,
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
    body: MCPOAuthStartIn,
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
            frontend_origin=body.frontend_origin,
        )
    except OAuthStartError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    return MCPOAuthStartOut(
        authorize_url=result.authorize_url,
        state=result.state,
        expires_at=result.expires_at,
    )


# ---------------------------------------------------------------------------
# Admin test-connection probe (spec §3.3).
# ---------------------------------------------------------------------------

_TEST_CONNECTION_TIMEOUT = 10.0


@router.post("/test-connection", response_model=TestConnectionOut)
async def admin_test_connection(
    body: TestConnectionIn,
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> TestConnectionOut:
    """Probe an MCP server URL without persisting anything.

    Used by the admin Custom-install form to validate the URL +
    optional static credential before creating the install row.
    Returns ``{ok, tool_count}`` on success or
    ``{ok=False, error_code, error_message}`` on transport / protocol
    failure.
    """
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
    except Exception as exc:  # noqa: BLE001 — any IO / protocol error surfaces as ok=False
        return TestConnectionOut(
            ok=False,
            tool_count=0,
            error_code=type(exc).__name__,
            error_message=str(exc)[:256],
        )
    return TestConnectionOut(ok=True, tool_count=len(discovery.tools))


# ---------------------------------------------------------------------------
# Admin connector list (GET /admin/mcp/connectors — spec §3.1).
# ---------------------------------------------------------------------------


@router.get("/connectors", response_model=AdminOrgConnectorListOut)
async def list_admin_connectors(
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> AdminOrgConnectorListOut:
    """List every org-scope install with its org-level effective state and
    a per-install workspace-distribution aggregate.

    Spec §3.1. Workspace-scope installs are excluded (admins find those
    via the workspace settings page). No workspace lens is applied — the
    admin row never carries per-workspace status.
    """
    org_installs = await svc._install_repo.list_org_installs()
    session = svc._install_repo.session
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
    workspace_repo = WorkspaceRepository(session)
    template_repo = MCPConnectorTemplateRepository(session)
    eligible = len(await workspace_repo.list_for_org(ctx.org_id))

    items: list[AdminOrgConnectorOut] = []
    for install in org_installs:
        org_grant = await grant_repo.get_org_grant(install.id)
        eff = derive_admin_org_effective(install, org_grant)
        state_rows = await state_repo.list_for_install(install.id)
        dist = build_workspace_distribution(
            install=install,
            state_rows=state_rows,
            eligible_workspace_count=eligible,
        )
        template_out: MCPConnectorTemplateOut | None = None
        if install.template_id is not None:
            template = await template_repo.get(install.template_id)
            if template is not None:
                template_out = _template_to_out(template)
        items.append(
            AdminOrgConnectorOut(
                install=_install_to_out(install),
                template=template_out,
                org_effective=eff,
                workspace_distribution=dist,
            )
        )
    return AdminOrgConnectorListOut(items=items)


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
      2. no org grant, ``install.auth_method == 'oauth'``,
         ``install.auth_status == 'pending'`` → pending_oauth.
      3. no org grant otherwise → missing_org_grant.
      4. org grant exists, ``grant_status == 'expired'``, no refresh
         available → grant_expired.
      5. org grant exists + ``discovery_status='error'`` → discovery_failed.
         Mirrors workspace ``compute_effective_state`` rule 10: only
         reported AFTER auth gates pass, because a discovery failure
         without an attached credential means the credential causing
         the failure is gone — so the right reason is "needs a grant",
         not "the (now-deleted) grant didn't work".
      6. org grant valid (or expired-with-refresh) → usable.
    """
    if install.auth_method == "none":
        return MCPAdminInstallEffectiveOut(install_id=install.id, usable=True, reason="usable")
    if org_grant is None:
        if install.auth_method == "oauth" and install.auth_status == "pending":
            return MCPAdminInstallEffectiveOut(
                install_id=install.id, usable=False, reason="pending_oauth"
            )
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="missing_org_grant"
        )
    # Org grant exists from here on.
    if org_grant.grant_status == "expired" and org_grant.refresh_credential_id is None:
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="grant_expired"
        )
    if install.discovery_status == "error":
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="discovery_failed"
        )
    # Valid OR expired-with-refresh — runtime token manager rotates the
    # access token on next call. Matches workspace-side
    # compute_effective_state rule 8 (only reports grant_expired when
    # there's no refresh credential).
    return MCPAdminInstallEffectiveOut(install_id=install.id, usable=True, reason="usable")


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
