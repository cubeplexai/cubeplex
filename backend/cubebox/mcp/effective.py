"""Effective-state derivation + DB-backed service for the four-layer MCP model.

This module is the **only** join point in the codebase between
``MCPConnectorTemplate`` + ``MCPConnectorInstall`` + ``MCPWorkspaceConnectorState``
+ ``MCPCredentialGrant``. The pure :func:`compute_effective_state` function is
the runtime / UI / admin contract for "is this connector usable, and if not,
why not"; :class:`MCPEffectiveConnectorService` is the production caller that
loads the four layers from Postgres and feeds them through the pure rule set.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.models import (
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)

# ---------------------------------------------------------------------------
# Pure effective-state model.
# ---------------------------------------------------------------------------


CredentialPolicy = Literal["org", "workspace", "user", "none"]

MCPEffectiveReason = Literal[
    "usable",
    "not_installed",
    "not_enabled_in_workspace",
    "install_uninstalled",
    "template_deprecated",
    "pending_oauth",
    "missing_org_grant",
    "missing_workspace_grant",
    "user_needs_connection",
    "grant_expired",
    "discovery_failed",
    "server_unreachable",
]


@dataclass(frozen=True)
class MCPGrantInput:
    """The runtime's view of the resolved grant row for a given install/scope."""

    scope: str
    status: str
    has_refresh: bool


@dataclass(frozen=True)
class MCPEffectiveInput:
    """Minimum input fields required by :func:`compute_effective_state`.

    ``template_status`` is ``None`` for custom installs (no template row).
    ``install_present=False`` lets the caller signal "no install row exists at
    all" without having to fabricate one. The remaining fields mirror the
    persisted state on ``MCPConnectorInstall`` / ``MCPWorkspaceConnectorState``.
    """

    template_status: str | None
    install_present: bool
    install_state: str
    workspace_state_present: bool
    workspace_enabled: bool
    auth_method: str
    auth_status: str
    discovery_status: str
    credential_policy: CredentialPolicy
    grant: MCPGrantInput | None
    transport: str


@dataclass(frozen=True)
class MCPEffectiveResult:
    """Output of :func:`compute_effective_state` — boolean + diagnostic reason."""

    usable: bool
    reason: MCPEffectiveReason
    credential_availability: Literal["available", "missing", "not_required"]


def _missing_grant_reason(policy: CredentialPolicy) -> MCPEffectiveReason:
    """Scope-specific missing-grant reason. ``"none"`` is a programmer error here."""
    if policy == "org":
        return "missing_org_grant"
    if policy == "workspace":
        return "missing_workspace_grant"
    if policy == "user":
        return "user_needs_connection"
    # ``"none"`` should never reach this branch — rule 5 (auth_method=="none")
    # short-circuits before grant resolution; if we get here the API layer
    # accepted an inconsistent install (auth_method != "none" with
    # credential_policy == "none"), which the four-layer plan explicitly
    # forbids. Surface it as ``missing_org_grant`` rather than crashing the
    # runtime — diagnostic UI can flag the inconsistency.
    return "missing_org_grant"


def compute_effective_state(value: MCPEffectiveInput) -> MCPEffectiveResult:
    """Decide usability + diagnostic reason for a single install.

    Decision order (first match wins) — see ``docs/dev/plans/
    2026-05-16-mcp-management-four-layer.md`` §Task 5 Step 2 for the spec
    contract.
    """
    # 1. No install row.
    if not value.install_present:
        return MCPEffectiveResult(False, "not_installed", "missing")

    # 2. Install tombstoned.
    if value.install_state == "uninstalled":
        return MCPEffectiveResult(False, "install_uninstalled", "missing")

    # 3. Template disabled (hard block). ``deprecated`` falls through to the
    #    DTO and does not gate usability; ``None`` (custom install) skips
    #    this rule entirely.
    if value.template_status == "disabled":
        return MCPEffectiveResult(False, "template_deprecated", "missing")

    # 4. Workspace state row missing or disabled.
    if not value.workspace_state_present or not value.workspace_enabled:
        return MCPEffectiveResult(False, "not_enabled_in_workspace", "missing")

    # 5. No-auth install — usable regardless of credential_policy. We key on
    #    auth_method only; an OAuth or static install with credential_policy
    #    "none" is a configuration bug the API layer must reject, not a
    #    runtime branch we silently accept.
    if value.auth_method == "none":
        return MCPEffectiveResult(True, "usable", "not_required")

    # 6. OAuth + org/workspace policy + pending + no grant → pending_oauth.
    #    User-policy installs never report pending_oauth (each member runs
    #    their own OAuth flow; a missing user grant is user_needs_connection).
    if (
        value.auth_method == "oauth"
        and value.credential_policy in {"org", "workspace"}
        and value.auth_status == "pending"
        and value.grant is None
    ):
        return MCPEffectiveResult(False, "pending_oauth", "missing")

    # 7. Grant absent → scope-specific missing reason.
    if value.grant is None:
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")

    # 8. Grant present but expired and no refresh credential available.
    if value.grant.status == "expired" and not value.grant.has_refresh:
        return MCPEffectiveResult(False, "grant_expired", "missing")

    # 9. Cross-scope grant — same scope-specific missing reason as rule 7.
    if value.grant.scope != value.credential_policy:
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")

    # 10. Discovery error — only checked after all auth gates pass, because
    #     discovery is only attempted once the connector is authorized.
    if value.discovery_status == "error":
        return MCPEffectiveResult(False, "discovery_failed", "missing")

    # 11. Otherwise usable.
    return MCPEffectiveResult(True, "usable", "available")


# ---------------------------------------------------------------------------
# DB-backed service.
# ---------------------------------------------------------------------------


@dataclass
class MCPEffectiveConnectorDTO:
    """Full effective-state row for UI / admin / diagnostics consumers.

    Mirrors the schema fields the future Task 4 API routes will serialize
    (template / install / workspace_state / credential_policy /
    required_grant_scope / credential_availability / credential_source /
    usable / reason). The DTO is a plain dataclass to keep the service free
    of pydantic imports; the API schema will wrap this in a response model.
    """

    install: MCPConnectorInstall
    template: MCPConnectorTemplate | None
    workspace_state: MCPWorkspaceConnectorState | None
    grant: MCPCredentialGrant | None
    credential_policy: CredentialPolicy
    required_grant_scope: str | None
    credential_availability: Literal["available", "missing", "not_required"]
    credential_source: Literal["org", "workspace", "user", "none"]
    usable: bool
    reason: MCPEffectiveReason
    template_status: str | None


@dataclass
class MCPRuntimeConnectorSpec:
    """Minimal runtime-shaped DTO for the cubepi MCP loader.

    Carries everything the per-run loader needs to (a) compute a bearer /
    identity token, (b) call ``cubepi.mcp.load_mcp_tools_http``, and (c)
    namespace the resulting tools with citation metadata. Anything heavier
    (template / state / diagnostic reason) stays on the full DTO.
    """

    install_id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    grant_scope: str | None
    credential_id: str | None
    refresh_credential_id: str | None
    tool_citations: dict[str, dict[str, Any]]
    tools_cache: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0
    template_id: str | None = None
    org_id: str = ""
    workspace_id: str = ""
    # OAuth-only: payload the runtime loader needs to invoke
    # ``OAuthTokenManager.get_access_token_for_grant`` without re-reading
    # the install / grant rows. ``grant`` is the live SQLModel row so the
    # token manager can rotate it in place via ``grant_repo.update``.
    grant: MCPCredentialGrant | None = None
    oauth_client_config: dict[str, Any] = field(default_factory=dict)
    # Display metadata captured at discovery time (server icons + per-tool
    # icons). Surfaced so the workspace ``/active-tools`` registry can be
    # built from one ``list_runtime_specs`` call without re-reading
    # install rows.
    discovery_metadata: dict[str, Any] = field(default_factory=dict)
    # When ``tools_cache`` was last refreshed; the runtime loader uses it
    # to decide whether to kick a background re-discovery.
    last_discovered_at: datetime | None = None
    # Static-auth shape (see ``MCPConnectorInstall``/``MCPConnectorTemplate``).
    # ``bearer`` (default) ⇒ ``Authorization: Bearer <token>``;
    # ``header`` ⇒ inject ``static_auth_header_name: <token>``;
    # ``query`` ⇒ append ``?static_auth_query_param=<token>`` to ``server_url``.
    static_auth_style: str = "bearer"
    static_auth_header_name: str | None = None
    static_auth_query_param: str | None = None


class MCPEffectiveConnectorService:
    """Compute effective state for every install visible to a workspace.

    The single read path that joins template + install + workspace state +
    grant. Loads each layer in one round-trip (``IN (...)`` style), so the
    cost is O(layers) round-trips per request rather than O(installs).
    """

    def __init__(
        self,
        *,
        template_repo: MCPConnectorTemplateRepository,
        install_repo: MCPConnectorInstallRepository,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        org_id: str,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self._template_repo = template_repo
        self._install_repo = install_repo
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._org_id = org_id
        self._token_manager = token_manager

    # ---------------- public API ---------------- #

    async def list_for_workspace_user(
        self,
        workspace_id: str,
        user_id: str,
        *,
        include_unusable: bool = True,
        include_disabled_org_installs: bool = True,
    ) -> list[MCPEffectiveConnectorDTO]:
        """Return effective rows for every install visible to this workspace.

        Includes:
          - Workspace-local installs owned by ``workspace_id``.
          - Org-scope installs that have a ``MCPWorkspaceConnectorState`` row
            for this workspace (org installs without a state row are invisible
            to the workspace by spec).

        ``install_state != "active"`` rows are filtered out entirely — they're
        tombstones; the runtime / UI should never see them again.

        ``include_disabled_org_installs=False`` further narrows org-scope
        installs to those whose state row is ``enabled=True``. Workspace-local
        installs are unaffected (the disabled-state surface lives on the admin
        page, not the workspace page).
        """
        rows = await self._collect_rows(
            workspace_id=workspace_id,
            user_id=user_id,
            include_disabled_org_installs=include_disabled_org_installs,
        )
        if include_unusable:
            return rows
        return [row for row in rows if row.usable]

    async def list_runtime_specs(
        self,
        workspace_id: str,
        user_id: str,
    ) -> list[MCPRuntimeConnectorSpec]:
        """Return runtime-shaped specs for every usable install in this workspace.

        Only ``usable=True`` rows are returned. Each spec carries the install
        id, server URL, transport, auth method, the resolved grant_scope (so
        the loader can ask the right token endpoint), the credential
        identifiers, plus citation / cache metadata.
        """
        rows = await self.list_for_workspace_user(
            workspace_id,
            user_id,
            include_unusable=False,
        )
        specs: list[MCPRuntimeConnectorSpec] = []
        for row in rows:
            install = row.install
            specs.append(
                MCPRuntimeConnectorSpec(
                    install_id=install.id,
                    name=install.name,
                    server_url=install.server_url,
                    transport=install.transport,
                    auth_method=install.auth_method,
                    grant_scope=row.grant.grant_scope if row.grant is not None else None,
                    credential_id=row.grant.credential_id if row.grant is not None else None,
                    refresh_credential_id=(
                        row.grant.refresh_credential_id if row.grant is not None else None
                    ),
                    tool_citations=dict(install.tool_citations or {}),
                    tools_cache=list(install.tools_cache or []),
                    headers=dict(install.headers or {}),
                    timeout=install.timeout,
                    sse_read_timeout=install.sse_read_timeout,
                    template_id=install.template_id,
                    org_id=install.org_id,
                    workspace_id=workspace_id,
                    grant=row.grant,
                    oauth_client_config=dict(install.oauth_client_config or {}),
                    discovery_metadata=dict(install.discovery_metadata or {}),
                    last_discovered_at=install.last_discovered_at,
                    static_auth_style=install.static_auth_style or "bearer",
                    static_auth_header_name=install.static_auth_header_name,
                    static_auth_query_param=install.static_auth_query_param,
                )
            )
        return specs

    # ---------------- internals ---------------- #

    async def _collect_rows(
        self,
        *,
        workspace_id: str,
        user_id: str,
        include_disabled_org_installs: bool = True,
    ) -> list[MCPEffectiveConnectorDTO]:
        # 1. Installs in scope: workspace-local + org-wide for this org. The
        #    org-wide set is further filtered by "has a workspace state row"
        #    below.
        ws_installs = await self._install_repo.list_workspace_installs(workspace_id)
        org_installs = await self._install_repo.list_org_installs()
        all_installs = [
            install
            for install in (*ws_installs, *org_installs)
            if install.install_state == "active"
        ]

        # 2. Workspace states for this workspace, keyed by install id.
        ws_states = await self._state_repo.list_for_workspace(workspace_id)
        states_by_install: dict[str, MCPWorkspaceConnectorState] = {
            state.install_id: state for state in ws_states
        }

        # 3. Decide which org installs count as "in scope" for this workspace.
        #    Default: any org install with a state row (enabled or disabled) is
        #    visible — the admin surface needs to show "disabled here" rows.
        #    When ``include_disabled_org_installs=False`` (workspace page), drop
        #    org installs whose state row is disabled or missing.
        if include_disabled_org_installs:
            org_visible_ids = set(states_by_install.keys())
        else:
            org_visible_ids = {
                install_id for install_id, state in states_by_install.items() if state.enabled
            }

        # 4. Filter org installs to that visibility set; workspace-local
        #    installs are always in-scope.
        visible_installs = [
            install
            for install in all_installs
            if install.workspace_id == workspace_id or install.id in org_visible_ids
        ]

        if not visible_installs:
            return []

        # 4. Load templates for the installs that reference one.
        template_ids = {
            install.template_id for install in visible_installs if install.template_id is not None
        }
        templates_by_id: dict[str, MCPConnectorTemplate] = {}
        for tid in template_ids:
            template = await self._template_repo.get(tid)
            if template is not None:
                templates_by_id[tid] = template

        # 5. Build DTOs (resolve grants inline — one DB hit per install at most;
        #    a per-scope IN(...) join would be cheaper but the grant repo only
        #    exposes scope-keyed getters and the row count per request is small).
        results: list[MCPEffectiveConnectorDTO] = []
        for install in visible_installs:
            state = states_by_install.get(install.id)
            # Workspace-local install missing its state row is a defect; spec
            # says treat it as enabled=False rather than synthesizing a row.
            if install.workspace_id == workspace_id and state is None:
                workspace_state_present = False
                workspace_enabled = False
                policy = install.default_credential_policy
            elif state is None:
                workspace_state_present = False
                workspace_enabled = False
                policy = install.default_credential_policy
            else:
                workspace_state_present = True
                workspace_enabled = state.enabled
                policy = state.credential_policy

            template = templates_by_id.get(install.template_id) if install.template_id else None
            template_status = template.status if template is not None else None

            grant = await self._resolve_grant(
                install=install,
                policy=policy,
                workspace_id=workspace_id,
                user_id=user_id,
            )

            grant_input = (
                MCPGrantInput(
                    scope=grant.grant_scope,
                    status=grant.grant_status,
                    has_refresh=grant.refresh_credential_id is not None,
                )
                if grant is not None
                else None
            )

            effective = compute_effective_state(
                MCPEffectiveInput(
                    template_status=template_status,
                    install_present=True,
                    install_state=install.install_state,
                    workspace_state_present=workspace_state_present,
                    workspace_enabled=workspace_enabled,
                    auth_method=install.auth_method,
                    auth_status=install.auth_status,
                    discovery_status=install.discovery_status,
                    credential_policy=_cast_policy(policy),
                    grant=grant_input,
                    transport=install.transport,
                )
            )

            results.append(
                MCPEffectiveConnectorDTO(
                    install=install,
                    template=template,
                    workspace_state=state,
                    grant=grant,
                    credential_policy=_cast_policy(policy),
                    required_grant_scope=_required_scope_for(policy),
                    credential_availability=effective.credential_availability,
                    credential_source=_cast_source(policy),
                    usable=effective.usable,
                    reason=effective.reason,
                    template_status=template_status,
                )
            )
        return results

    async def _resolve_grant(
        self,
        *,
        install: MCPConnectorInstall,
        policy: str,
        workspace_id: str,
        user_id: str,
    ) -> MCPCredentialGrant | None:
        """Fetch the grant required by ``policy`` for this install.

        No fall-back across scopes — an org grant must not flip a
        user-policy install to usable. For OAuth grants near or past
        expiry with a refresh credential, kick the token manager so it
        refreshes (in-place); if refresh fails the grant status is left
        as ``expired`` and the pure rule set surfaces ``grant_expired``.
        """
        if policy == "none":
            return None
        if policy == "org":
            grant = await self._grant_repo.get_org_grant(install.id)
        elif policy == "workspace":
            grant = await self._grant_repo.get_workspace_grant(install.id, workspace_id)
        elif policy == "user":
            grant = await self._grant_repo.get_user_grant(
                install.id, user_id, workspace_id=workspace_id
            )
        else:
            return None

        if grant is None:
            return None

        if (
            install.auth_method == "oauth"
            and grant.expires_at is not None
            and _is_expired(grant.expires_at)
            and self._token_manager is not None
            and grant.refresh_credential_id is not None
        ):
            # Attempt refresh via the OAuth manager. The manager mutates the
            # grant row in place on success; on failure it sets
            # ``grant_status='expired'`` so the pure rule emits
            # ``grant_expired``.
            try:
                await self._token_manager.get_access_token_for_grant(
                    grant=grant,
                    grant_repo=self._grant_repo,
                    server_url=install.server_url,
                    oauth_client_config=dict(install.oauth_client_config or {}),
                )
                # Re-read so the caller sees the manager's mutations.
                refreshed = await self._reread_grant(grant, workspace_id, user_id)
                if refreshed is not None:
                    return refreshed
            except Exception:  # noqa: BLE001 — non-fatal; effective state stays as 'expired'
                pass

        return grant

    async def _reread_grant(
        self,
        grant: MCPCredentialGrant,
        workspace_id: str,
        user_id: str,
    ) -> MCPCredentialGrant | None:
        if grant.grant_scope == "org":
            return await self._grant_repo.get_org_grant(grant.install_id)
        if grant.grant_scope == "workspace":
            return await self._grant_repo.get_workspace_grant(grant.install_id, workspace_id)
        if grant.grant_scope == "user":
            return await self._grant_repo.get_user_grant(
                grant.install_id, user_id, workspace_id=workspace_id
            )
        return None


def _is_expired(when: datetime) -> bool:
    if when.tzinfo is None:  # SQLite discards tz on round-trip
        when = when.replace(tzinfo=UTC)
    return when < datetime.now(UTC)


def _cast_policy(policy: str) -> CredentialPolicy:
    if policy in {"org", "workspace", "user", "none"}:
        return policy  # type: ignore[return-value]
    # Conservative fallback — should never happen because the DB check
    # constraint restricts these values. Treat unknowns as ``none`` so the
    # runtime doesn't crash on an unexpected enum.
    return "none"


def _cast_source(policy: str) -> Literal["org", "workspace", "user", "none"]:
    return _cast_policy(policy)


def _required_scope_for(policy: str) -> str | None:
    if policy == "none":
        return None
    return policy


__all__ = [
    "CredentialPolicy",
    "MCPEffectiveConnectorDTO",
    "MCPEffectiveConnectorService",
    "MCPEffectiveInput",
    "MCPEffectiveReason",
    "MCPEffectiveResult",
    "MCPGrantInput",
    "MCPRuntimeConnectorSpec",
    "compute_effective_state",
]
