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

from cubepi.mcp import load_mcp_tools_http
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp.cubepi_runtime import MCPTransport
from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.mcp.exceptions import MCPDiscoveryFailed
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.services.credential import CredentialService

_DISCOVERY_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DiscoveryResult:
    install_id: str
    discovery_status: str  # "ok" | "error"
    tool_count: int
    tools_cache_raw: list[dict[str, Any]]
    last_error: str | None


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    """Normalise ``load_mcp_tools_http`` results to ``{name, description,
    input_schema}`` dicts.

    Real ``AgentTool`` instances carry the JSON Schema indirectly through
    a synthesized pydantic model on ``.parameters``; we recover it via
    ``model_json_schema()``. Test stubs that already expose
    ``input_schema`` directly fall through the ``getattr`` path.
    """
    name = getattr(tool, "name", "")
    description = getattr(tool, "description", None)
    input_schema: dict[str, Any] | None = getattr(tool, "input_schema", None)
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
    }


def _build_runtime_spec_for_discovery(install: Any, grant: Any) -> Any:
    """Build the ``MCPRuntimeConnectorSpec`` shape that
    ``_resolve_headers_from_spec`` expects without going through the
    full effective-state list (which the caller already computed)."""
    from cubebox.mcp.effective import MCPRuntimeConnectorSpec

    return MCPRuntimeConnectorSpec(
        install_id=install.id,
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
    )


async def discover_tools_for_install(
    *,
    install_id: str,
    workspace_id: str | None,
    actor_user_id: str,
    session: AsyncSession,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_mgr: OAuthTokenManager,
) -> DiscoveryResult:
    """Refresh discovery for a single install.

    Routes inject ``signer`` and ``token_mgr`` via the existing DI
    factories. Both are needed because ``_resolve_headers_from_spec``
    mints an identity token for ``auth_method='none'`` installs and
    refreshes OAuth grants on call for ``auth_method='oauth'`` installs.
    """
    cred_org_id = cred_service._org_id
    assert cred_org_id is not None, "discover_tools_for_install requires org-scoped cred_service"
    install_repo = MCPConnectorInstallRepository(session, org_id=cred_org_id)
    install = await install_repo.get(install_id)
    if install is None:
        raise ValueError("connector_install_not_found")
    if install.install_state != "active":
        raise ValueError("connector_install_not_active")

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=install.org_id)
    grant_repo = MCPCredentialGrantRepository(session, org_id=install.org_id)
    template_repo = MCPConnectorTemplateRepository(session)
    effective_svc = MCPEffectiveConnectorService(
        template_repo=template_repo,
        install_repo=install_repo,
        state_repo=state_repo,
        grant_repo=grant_repo,
        org_id=install.org_id,
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
        dto = next((d for d in dtos if d.install.id == install_id), None)
        if dto is None:
            raise ValueError("connector_install_not_found")
        usable = dto.usable
        reason = dto.reason
        grant = dto.grant
    else:
        grant = await grant_repo.get_org_grant(install_id)
        usable = install.auth_method == "none" or (
            grant is not None and grant.grant_status == "valid"
        )
        reason = "usable" if usable else "missing_org_grant"

    if not usable:
        raise MCPDiscoveryFailed(f"connector_not_usable:{reason}")

    from cubebox.mcp.cubepi_runtime import _resolve_headers_from_spec

    spec = _build_runtime_spec_for_discovery(install=install, grant=grant)
    headers = await _resolve_headers_from_spec(
        spec=spec,
        workspace_id=workspace_id or install.workspace_id or "",
        org_id=install.org_id,
        user_id=actor_user_id,
        cred_service=cred_service,
        signer=signer,
        token_manager=token_mgr,
        grant_repo=grant_repo,
    )
    if headers is None:
        install.discovery_status = "error"
        install.last_error = "Auth header resolution failed"
        await install_repo.update(install)
        return DiscoveryResult(
            install_id=install_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )

    try:
        tools = await asyncio.wait_for(
            load_mcp_tools_http(
                install.server_url,
                headers=headers or None,
                timeout=install.timeout,
                transport=cast(MCPTransport, install.transport),
            ),
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP discovery failed for {}: {}", install_id, exc)
        install.discovery_status = "error"
        install.last_error = str(exc)[:2048]
        await install_repo.update(install)
        return DiscoveryResult(
            install_id=install_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )

    tools_cache_raw: list[dict[str, Any]] = [_tool_to_dict(t) for t in tools]
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
    install.discovery_status = "ok"
    install.last_error = None
    await install_repo.update(install)
    return DiscoveryResult(
        install_id=install_id,
        discovery_status="ok",
        tool_count=len(tools_cache_raw),
        tools_cache_raw=tools_cache_raw,
        last_error=None,
    )
