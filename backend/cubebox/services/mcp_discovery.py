"""MCP tool discovery for the restore-lost-UI Refresh tools flow.

Replaces the legacy ``cubepi_admin_refresh.py`` that was deleted in
commit 243e6396. Reuses the runtime path's ``load_mcp_tools_http``
cubepi helper and writes the result into the install row's
``tools_cache`` / ``discovery_status`` / ``last_error`` fields.

Per spec §3.2:

* Caller-grant policy: use the effective grant resolved by the
  install's policy (org / workspace / user). Mirrors agent runtime;
  no cross-scope fallback.
* 30-second cubepi timeout.
* On exception: catch and persist ``discovery_status='error' +
  last_error=str(exc)``; do NOT raise — return the result with
  status='error' so the route layer can decide.

The legacy code converted ``mcp`` SDK descriptors (``desc.inputSchema``)
directly to ``{name, description, input_schema}`` dicts. ``load_mcp_tools_http``
returns ``AgentTool`` instances whose ``parameters`` is a synthesized
pydantic model. We expose the original JSON Schema via
``parameters.model_json_schema()`` when the loader returns ``AgentTool``;
tests stub the loader with ``SimpleNamespace`` objects that already
carry ``input_schema``, so the helper accepts either shape.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

from cubepi.mcp.http_loader import _open_session
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import slugify_for_namespace
from cubebox.mcp.cubepi_runtime import MCPTransport
from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.mcp.exceptions import MCPDiscoveryFailed
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.services.credential import CredentialService

_DISCOVERY_TIMEOUT_SECONDS = 30.0


def _format_discovery_error(exc: BaseException) -> str:
    """Produce a human-readable error string for ``install.last_error``.

    The MCP SDK opens the session through an ``async with`` block backed
    by an asyncio ``TaskGroup`` (and httpx adds its own connection-level
    task groups), so a 401 from the MCP server bubbles out as
    ``ExceptionGroup("unhandled errors in a TaskGroup", [...])``. Showing
    that to the operator is useless — they need to see the actual cause.
    Unwrap one or more layers of ExceptionGroup, prefer the first
    non-group inner exception, and fall back to the outer message only
    when the group is empty (shouldn't happen but defensive).
    """
    inner: BaseException = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    name = type(inner).__name__
    msg = str(inner) or repr(inner)
    return f"{name}: {msg}"


@dataclass(frozen=True)
class DiscoveryResult:
    connector_id: str
    discovery_status: str  # "ok" | "error"
    tool_count: int
    tools_cache_raw: list[dict[str, Any]]
    last_error: str | None


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    """Normalise an MCP ``Tool`` descriptor (or a test stub) into the
    ``{name, description, input_schema, output_schema}`` dict shape we
    persist in ``tools_cache``.

    Real ``mcp.types.Tool`` instances expose camelCase ``inputSchema`` /
    ``outputSchema``; test stubs typically already use snake_case
    ``input_schema`` / ``output_schema``. Both are accepted.
    """
    name = getattr(tool, "name", "")
    description = getattr(tool, "description", None)
    input_schema: dict[str, Any] | None = getattr(tool, "input_schema", None) or getattr(
        tool, "inputSchema", None
    )
    output_schema: dict[str, Any] | None = getattr(tool, "output_schema", None) or getattr(
        tool, "outputSchema", None
    )
    # AgentTool path (legacy): synthesised pydantic model on .parameters.
    if input_schema is None:
        params = getattr(tool, "parameters", None)
        if params is not None and hasattr(params, "model_json_schema"):
            try:
                input_schema = params.model_json_schema()
            except Exception:  # noqa: BLE001 — fall back to empty schema on any error
                input_schema = None
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }


@dataclass(frozen=True)
class _DiscoveredRaw:
    """Raw output of one discovery handshake.

    ``tools`` are ``mcp.types.Tool`` descriptors preserving the full
    schema fields (notably ``outputSchema`` which cubepi's AgentTool
    wrapper drops). ``init_result`` is the ``InitializeResult`` carrying
    ``serverInfo`` (name + icons + websiteUrl).
    """

    tools: list[Any]
    init_result: Any


async def _list_raw_mcp_tools(
    server_url: str,
    *,
    headers: dict[str, str] | None,
    timeout: float,
    transport: MCPTransport,
) -> _DiscoveredRaw:
    """Open an MCP session, call ``initialize`` + ``list_tools``, and
    return both the raw tool descriptors and the initialize result.

    We bypass ``cubepi.mcp.load_mcp_tools_http`` because its
    ``AgentTool`` wrapper drops the optional ``outputSchema`` field —
    citation editing needs it to suggest output field names. Tool
    invocation still goes through cubepi (Try It path); only discovery
    talks to MCP directly here.

    The initialize result is captured here (rather than in a separate
    handshake) so we can surface the server's display metadata —
    ``Implementation.icons`` + ``websiteUrl`` from MCP spec rev
    2025-11-25 — to the frontend's tool registry without an extra RTT.
    """
    async with _open_session(server_url, headers=headers, timeout=timeout, transport=transport) as (
        session,
        _get_session_id,
    ):
        init_result = await asyncio.wait_for(session.initialize(), timeout=timeout)
        tools_resp = await asyncio.wait_for(session.list_tools(), timeout=timeout)
    return _DiscoveredRaw(tools=list(tools_resp.tools), init_result=init_result)


def _icon_to_dict(icon: Any) -> dict[str, Any]:
    """Serialise an ``MCPIcon`` (or duck-type) to the JSON dict shape
    persisted in ``discovery_metadata``."""
    sizes = getattr(icon, "sizes", None)
    return {
        "src": getattr(icon, "src", ""),
        "mime_type": getattr(icon, "mime_type", None),
        "sizes": list(sizes) if sizes else None,
        "theme": getattr(icon, "theme", None),
    }


async def _build_discovery_metadata(discovered: _DiscoveredRaw) -> dict[str, Any]:
    """Build the JSON shape persisted in ``MCPConnector.discovery_metadata``.

    Server icons + websiteUrl come from ``InitializeResult.serverInfo``;
    per-tool icons come from each ``Tool.icons``. Tools without icons are
    omitted from ``tool_icons`` (keeps the JSON small for installs whose
    server didn't bother).

    Server ``https`` icons are best-effort materialised into ``cached_src``
    (``data:`` URI) so air-gapped browsers can still render a logo when the
    backend could reach the vendor CDN. Failures leave the original ``src``
    and never fail discovery.
    """
    from cubepi.mcp.types import icons_from_raw, server_info_from_init_result

    from cubebox.mcp.icons import enrich_server_icons

    server = server_info_from_init_result(discovered.init_result)
    server_dict: dict[str, Any] | None = None
    if server is not None:
        raw_icons = [_icon_to_dict(i) for i in server.icons]
        server_dict = {
            "name": server.name,
            "version": server.version,
            "website_url": server.website_url,
            "icons": await enrich_server_icons(raw_icons),
        }

    tool_icons: dict[str, list[dict[str, Any]]] = {}
    for tool in discovered.tools:
        icons = icons_from_raw(getattr(tool, "icons", None))
        if not icons:
            continue
        # Tool icons are not materialised (can be large); UI uses src + onError.
        tool_icons[getattr(tool, "name", "")] = [_icon_to_dict(i) for i in icons]

    return {"server": server_dict, "tool_icons": tool_icons}


def _build_runtime_spec_for_discovery(install: Any, grant: Any) -> Any:
    """Build the ``MCPRuntimeConnectorSpec`` shape that
    ``_resolve_auth_from_spec`` expects without going through the
    full effective-state list (which the caller already computed)."""
    from cubebox.mcp.effective import MCPRuntimeConnectorSpec

    return MCPRuntimeConnectorSpec(
        connector_id=install.id,
        name=install.name,
        server_url=install.server_url,
        transport=install.transport,
        auth_method=install.auth_method,
        grant_scope=grant.grant_scope if grant is not None else None,
        credential_id=grant.credential_id if grant is not None else None,
        refresh_credential_id=(grant.refresh_credential_id if grant is not None else None),
        tool_citations={},  # not needed for discovery
        tools_cache=[],
        headers=dict(install.headers or {}),
        timeout=install.timeout,
        sse_read_timeout=install.sse_read_timeout,
        template_id=install.template_id,
        org_id=install.org_id,
        workspace_id=install.workspace_id or "",
        grant=grant,
        oauth_client_config=dict(install.oauth_client_config or {}),
        static_auth_style=getattr(install, "static_auth_style", None) or "bearer",
        static_auth_header_name=getattr(install, "static_auth_header_name", None),
        static_auth_query_param=getattr(install, "static_auth_query_param", None),
    )


async def run_post_grant_discovery(
    *,
    connector_id: str,
    workspace_id: str | None,
    actor_user_id: str,
    session: AsyncSession,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_mgr: OAuthTokenManager,
) -> None:
    """Best-effort discovery after a grant lands.

    Called from the static-grant POST routes and the OAuth callback so
    the operator gets immediate feedback on whether the credential
    actually works against the MCP server, instead of "saved" with no
    validation. ``discover_tools_for_install`` already persists
    successes / failures into install.discovery_status / last_error
    for the call paths it knows about (header resolution + tool list).

    Any exception from here must not bubble up to the caller — the
    grant has already been committed, and a 500 from discovery would
    leave the client thinking the save failed even though it didn't.
    Swallow everything except control-flow exceptions (CancelledError,
    KeyboardInterrupt, SystemExit) and log so the failure is at least
    visible in server logs. The next discovery run (manual Retry or
    runtime use) will re-attempt and persist its own state.
    """
    try:
        await discover_tools_for_install(
            connector_id=connector_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,
        )
    except (MCPDiscoveryFailed, ValueError) as exc:
        logger.warning("Post-grant discovery skipped for {}: {}", connector_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Post-grant discovery raised unexpectedly for {}: {}", connector_id, exc)


async def discover_tools_for_install(
    *,
    connector_id: str,
    workspace_id: str | None,
    actor_user_id: str,
    session: AsyncSession,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_mgr: OAuthTokenManager,
) -> DiscoveryResult:
    """Refresh discovery for a single install.

    Routes inject ``signer`` and ``token_mgr`` via the existing DI
    factories. Both are needed because ``_resolve_auth_from_spec``
    mints an identity token for ``auth_method='none'`` installs and
    refreshes OAuth grants on call for ``auth_method='oauth'`` installs.
    """
    cred_org_id = cred_service._org_id
    assert cred_org_id is not None, "discover_tools_for_install requires org-scoped cred_service"
    install_repo = MCPConnectorRepository(session, org_id=cred_org_id)
    install = await install_repo.get(connector_id)
    if install is None:
        raise ValueError("connector_install_not_found")
    if install.install_state != "active":
        raise ValueError("connector_install_not_active")
    connector_repo = MCPConnectorRepository(session, org_id=install.org_id)
    connector = await connector_repo.get_active_by_identity(
        template_id=install.template_id,
        server_url_hash=install.server_url_hash,
        slug_name=slugify_for_namespace(install.name),
    )

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=install.org_id)
    grant_repo = MCPCredentialGrantRepository(session, org_id=install.org_id)
    template_repo = MCPConnectorTemplateRepository(session)
    effective_svc = MCPEffectiveConnectorService(
        template_repo=template_repo,
        install_repo=install_repo,
        state_repo=state_repo,
        grant_repo=grant_repo,
        org_id=install.org_id,
        # Pass token_manager so _resolve_grant can rotate expired
        # OAuth tokens proactively, matching agent runtime. Without
        # this, an OAuth install whose access token has expired but
        # has a refresh credential would surface as unusable from
        # the effective DTO and discovery would 400 before
        # _resolve_auth_from_spec gets a chance to refresh.
        token_manager=token_mgr,
    )

    if workspace_id is None and install.install_scope == "workspace":
        workspace_id = install.workspace_id
    if workspace_id is None and install.default_credential_policy in {"workspace", "user"}:
        raise ValueError("workspace_id_required_for_scoped_policy")

    grant = None
    if workspace_id is not None:
        dtos = await effective_svc.list_for_workspace_user(
            workspace_id, actor_user_id, include_unusable=True
        )
        dto = next((d for d in dtos if d.install.id == connector_id), None)
        if dto is None:
            raise ValueError("connector_install_not_found")
        usable = dto.usable
        reason = dto.reason
        grant = dto.grant
        # Retry path: if the connector is unusable ONLY because the
        # previous discovery left `discovery_status='error'`, allow
        # this run to proceed so the Retry button can actually clear
        # the persisted error. The effective service emits
        # `reason='discovery_failed'` for that state; treat it as
        # transient-usable here. Other unusable reasons (missing
        # grant, expired without refresh, etc.) still block.
        if not usable and reason == "discovery_failed":
            usable = True
    else:
        grant = await grant_repo.get_org_grant(connector_id)
        # Match the workspace-side effective rule (compute_effective_state
        # rule 8): an org OAuth grant whose status is 'expired' but still
        # has a refresh_credential_id is usable — the token manager rotates
        # the access token on call. Rejecting it here would block Refresh
        # tools for any org OAuth install that ever had a transient
        # refresh failure mark the grant expired.
        usable = install.auth_method == "none" or (
            grant is not None
            and (
                grant.grant_status == "valid"
                or (grant.grant_status == "expired" and grant.refresh_credential_id is not None)
            )
        )
        reason = "usable" if usable else "missing_org_grant"

    if not usable:
        raise MCPDiscoveryFailed(f"connector_not_usable:{reason}")

    from cubebox.mcp.cubepi_runtime import _resolve_auth_from_spec

    spec = _build_runtime_spec_for_discovery(install=install, grant=grant)
    # Wrap header resolution: vault read failures (deleted credential,
    # wrong kind, OAuth refresh failure) raise from
    # ``_resolve_auth_from_spec``. Persist them as
    # discovery_status='error' + last_error so the banner surfaces
    # them; never bubble as a 500.
    try:
        resolved = await _resolve_auth_from_spec(
            spec=spec,
            workspace_id=workspace_id or install.workspace_id or "",
            org_id=install.org_id,
            user_id=actor_user_id,
            cred_service=cred_service,
            signer=signer,
            token_manager=token_mgr,
            grant_repo=grant_repo,
        )
    except Exception as exc:  # noqa: BLE001
        install.discovery_status = "error"
        install.last_error = f"credential_resolution_failed: {_format_discovery_error(exc)}"[:2048]
        if connector is not None:
            connector.discovery_status = install.discovery_status
            connector.last_error = install.last_error
            await connector_repo.update(connector)
        await install_repo.update(install)
        return DiscoveryResult(
            connector_id=connector_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )
    if resolved is None:
        install.discovery_status = "error"
        install.last_error = "Auth header resolution failed"
        if connector is not None:
            connector.discovery_status = install.discovery_status
            connector.last_error = install.last_error
            await connector_repo.update(connector)
        await install_repo.update(install)
        return DiscoveryResult(
            connector_id=connector_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )
    headers, server_url = resolved

    try:
        discovered = await asyncio.wait_for(
            _list_raw_mcp_tools(
                server_url,
                headers=headers or None,
                timeout=install.timeout,
                transport=cast(MCPTransport, install.transport),
            ),
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        formatted = _format_discovery_error(exc)
        logger.warning("MCP discovery failed for {}: {}", connector_id, formatted)
        install.discovery_status = "error"
        install.last_error = formatted[:2048]
        if connector is not None:
            connector.discovery_status = install.discovery_status
            connector.last_error = install.last_error
            await connector_repo.update(connector)
        await install_repo.update(install)
        return DiscoveryResult(
            connector_id=connector_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )

    tools_cache_raw: list[dict[str, Any]] = [_tool_to_dict(t) for t in discovered.tools]
    # Strip orphan citation mapping keys whose tool no longer exists in
    # the freshly discovered tools_cache. Mirrors the legacy refresh
    # behavior: on success, tools_cache is authoritative.
    current_names = {t["name"] for t in tools_cache_raw if t.get("name")}
    existing_citations = dict(install.tool_citations or {})
    if existing_citations:
        cleaned = {k: v for k, v in existing_citations.items() if k in current_names}
        if cleaned != existing_citations:
            install.tool_citations = cleaned

    install.tools_cache = tools_cache_raw
    install.discovery_metadata = await _build_discovery_metadata(discovered)
    install.discovery_status = "ok"
    install.last_error = None
    if connector is not None:
        connector.tools_cache = tools_cache_raw
        connector.discovery_metadata = install.discovery_metadata
        connector.discovery_status = install.discovery_status
        connector.last_error = install.last_error
        await connector_repo.update(connector)
    await install_repo.update(install)
    return DiscoveryResult(
        connector_id=connector_id,
        discovery_status="ok",
        tool_count=len(tools_cache_raw),
        tools_cache_raw=tools_cache_raw,
        last_error=None,
    )
