"""Install / workspace-state / credential-grant service primitives.

This module owns the **service-level** invariants of the four-layer MCP
model (``MCPConnectorTemplate`` → ``MCPConnectorInstall`` →
``MCPWorkspaceConnectorState`` + ``MCPCredentialGrant``):

* Pure derivation of install defaults from a chosen ``auth_method`` and a
  requested ``credential_policy`` (the ``auth_method=='none'`` short
  circuit is a hard invariant, not a nicety).
* Atomic install creation: the install row and at least one (workspace-
  scope) or zero/many (org-scope distribution) ``WorkspaceConnectorState``
  rows are written in the same transaction, so a failure in either half
  rolls both back. This is what keeps "phantom installs with no state"
  out of the DB.
* Strict scope-vs-fk validation on ``create_static_grant`` that mirrors
  the DB ``ck_mcp_credential_grants_scope_columns`` check exactly. The
  check is repeated at the service layer because the grant write is
  preceded by a vault write that we don't want to perform when the
  shape is wrong (otherwise a 400 from the DB would leave a dangling
  encrypted credential).

Anything route-shaped (request DTOs, HTTPException mapping, etc.) lives
in the routes layer; this module never imports from ``cubebox.api``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from loguru import logger

from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, server_url_hash, slugify_for_namespace
from cubebox.models import (
    MCPConnector,
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.repositories.workspace import WorkspaceRepository
from cubebox.services.credential import CredentialService


@dataclass(frozen=True)
class MCPInstallDefaults:
    """Derived defaults applied to a fresh install row.

    ``auth_status`` and ``credential_policy`` are persisted on the
    ``MCPConnectorInstall`` row. Callers are expected to use the pure
    derivation in :func:`install_defaults_for_auth_method` rather than
    inlining the rules — the ``auth_method=='none'`` short circuit must
    NOT be re-derived ad-hoc, otherwise an installer that picked
    ``credential_policy='user'`` plus ``auth_method='none'`` would write
    a no-auth install that the runtime then asks for a per-user grant
    against, which can never exist.
    """

    auth_status: str
    credential_policy: str


def install_defaults_for_auth_method(auth_method: str, requested_policy: str) -> MCPInstallDefaults:
    """Translate user intent into the install row's stored defaults.

    Invariants:

    * ``auth_method == "none"`` collapses ``credential_policy`` to
      ``"none"`` and ``auth_status`` to ``"not_required"`` regardless of
      what the caller requested. A no-auth connector has no grants by
      construction, so allowing a user-scope policy here would create
      an install that is forever in an "expecting a grant that can
      never be created" state.
    * Otherwise the requested policy is preserved verbatim and the
      install starts in ``auth_status="pending"`` — the actual grant
      write (or OAuth callback) flips it to ``"connected"`` downstream.
    """
    if auth_method == "none":
        return MCPInstallDefaults(auth_status="not_required", credential_policy="none")
    return MCPInstallDefaults(auth_status="pending", credential_policy=requested_policy)


class MCPConnectorInstallService:
    """Service-level orchestration for install / state / grant writes.

    Construction is intentionally repo-flavoured rather than session-
    flavoured: callers (DI providers in ``cubebox.mcp.dependencies``)
    instantiate the three org-scoped repos once and pass them in, which
    keeps this class free of any session/transaction wiring concerns
    and lets the unit tests inject mocks without touching SQLModel at
    all.

    ``org_id`` and ``actor_user_id`` are stored on the service so every
    write stamps the same audit identity without the routes having to
    re-pass it on each call. They must already match the org_id baked
    into ``install_repo`` / ``state_repo`` / ``grant_repo`` — passing
    mismatched values here is a programming error, not a runtime
    branch, so we don't re-validate it.
    """

    def __init__(
        self,
        install_repo: MCPConnectorInstallRepository,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        cred_service: CredentialService,
        *,
        org_id: str,
        actor_user_id: str,
        workspace_repo: WorkspaceRepository | None = None,
        connector_repo: MCPConnectorRepository | None = None,
    ) -> None:
        self._install_repo = install_repo
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._cred_service = cred_service
        self._org_id = org_id
        self._actor_user_id = actor_user_id
        # Optional because the unit tests don't need org-distribution fan-out and
        # constructing a real ``WorkspaceRepository`` would force them to bring
        # along a session fixture. DI providers wire this for real.
        self._workspace_repo = workspace_repo
        self._connector_repo = connector_repo

    # ------------------------------------------------------------------ install create
    async def create_from_template_for_workspace(
        self,
        *,
        template: MCPConnectorTemplate,
        workspace_id: str,
        auth_method: str,
        credential_policy: str,
    ) -> MCPConnectorInstall:
        """Materialize a workspace-scope install + its enablement state.

        The state row is upserted with ``enabled=True`` and
        ``enablement_source="workspace_manual"`` because the only way to
        reach this method is a workspace member explicitly installing
        a connector for their workspace.

        Atomicity: both writes go through the repos which each commit
        independently in the current codebase. If the state upsert
        raises after the install row is persisted, the install row is
        still committed — we rely on the install's ``install_state``
        defaulting to ``'active'`` and the partial unique index
        excluding ``'uninstalled'`` rows so a retry path can reach the
        same shape on a second attempt. A future refactor to a single
        ``async with session.begin()`` block is the right home for true
        atomicity; the spec calls that out as a follow-up and the
        plan accepts the looser guarantee here.

        ``auth_method`` is cross-checked against
        ``template.supported_auth_methods`` — a direct API call that picks
        e.g. ``auth_method='none'`` against a static-only template would
        otherwise produce an install whose runtime credential resolution
        is unreachable. ``ValueError("auth_method_not_supported_by_template")``
        is raised before any DB write.
        """
        if auth_method not in template.supported_auth_methods:
            raise ValueError("auth_method_not_supported_by_template")
        connector = await self._ensure_connector_from_template(template, auth_method=auth_method)
        conflict = await self._get_install_conflict(
            server_url_hash=server_url_hash(template.server_url),
            name=template.name,
            template_id=template.id,
            exclude_id=None,
        )
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        if conflict is not None and conflict.workspace_id == workspace_id:
            conflict = await self.promote_workspace_install_to_org(
                install_id=conflict.id,
                distribution={"mode": "none"},
            )
        if conflict is not None and conflict.workspace_id is not None:
            raise ValueError("install_already_exists")
        if conflict is not None and conflict.template_id != template.id:
            raise ValueError("install_already_exists")
        if conflict is not None:
            await self._state_repo.upsert_for_connector(
                workspace_id=workspace_id,
                install_id=conflict.id,
                connector_id=connector.id,
                enabled=True,
                credential_policy=defaults.credential_policy,
                enablement_source="workspace_manual",
                updated_by_user_id=self._actor_user_id,
            )
            conflict.__dict__["_connector_id"] = connector.id
            return conflict
        install = MCPConnectorInstall(
            org_id=self._org_id,
            workspace_id=None,
            install_scope="org",
            template_id=template.id,
            name=template.name,
            server_url=template.server_url,
            server_url_hash=server_url_hash(template.server_url),
            transport=template.transport,
            auth_method=auth_method,
            default_credential_policy=defaults.credential_policy,
            auth_status=defaults.auth_status,
            tool_citations=dict(template.tool_citation_defaults),
            static_auth_style=template.static_auth_style,
            static_auth_header_name=template.static_auth_header_name,
            static_auth_query_param=template.static_auth_query_param,
            auto_enroll_new_workspaces=False,
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._install_repo.add(install)
        saved.__dict__["_connector_id"] = connector.id
        await self._state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            install_id=saved.id,
            connector_id=connector.id,
            enabled=True,
            credential_policy=defaults.credential_policy,
            enablement_source="workspace_manual",
            updated_by_user_id=self._actor_user_id,
        )
        return saved

    async def create_from_template_for_org(
        self,
        *,
        template: MCPConnectorTemplate,
        auth_method: str,
        credential_policy: str,
        distribution: dict[str, Any],
    ) -> MCPConnectorInstall:
        """Materialize an org-scope install + zero/many enablement rows.

        ``distribution`` shape:

        * ``{"mode": "all"}`` — auto-enable in every current workspace
          in the org; rows get ``enablement_source="admin_auto"``.
        * ``{"mode": "selected", "workspace_ids": [...]}`` — only the
          listed workspaces; rows get ``enablement_source="admin_manual"``.
        * ``{"mode": "none"}`` — install row only, no state rows. The
          admin can selectively enable workspaces later.

        Unknown modes raise ``ValueError`` so a typo in the route layer
        surfaces as a 400 rather than a silently-empty fan-out.

        For ``mode='selected'`` every requested workspace id is validated
        against the org's actual workspaces BEFORE the install row is
        persisted — a bad id raises ``ValueError("workspace_not_in_org")``
        with zero rows written, so a typo cannot leave behind a phantom
        install with no state rows.

        ``auth_method`` is cross-checked against
        ``template.supported_auth_methods`` — a direct API call that picks
        e.g. ``auth_method='none'`` against a static-only template would
        otherwise produce an install whose runtime credential resolution
        is unreachable. ``ValueError("auth_method_not_supported_by_template")``
        is raised before any DB write.
        """
        if auth_method not in template.supported_auth_methods:
            raise ValueError("auth_method_not_supported_by_template")
        connector = await self._ensure_connector_from_template(template, auth_method=auth_method)
        conflict = await self._get_install_conflict(
            server_url_hash=server_url_hash(template.server_url),
            name=template.name,
            template_id=template.id,
            exclude_id=None,
        )
        workspace_ids, enablement_source, mode = await self._resolve_distribution(distribution)
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        if conflict is not None and conflict.workspace_id is None:
            existing_states = await self._state_repo.list_for_install(conflict.id)
            if not any(state.enablement_source == "workspace_manual" for state in existing_states):
                raise ValueError("install_already_exists")
            conflict.auth_method = auth_method
            conflict.default_credential_policy = defaults.credential_policy
            conflict.auth_status = defaults.auth_status
            conflict.tool_citations = dict(template.tool_citation_defaults)
            conflict.static_auth_style = template.static_auth_style
            conflict.static_auth_header_name = template.static_auth_header_name
            conflict.static_auth_query_param = template.static_auth_query_param
            conflict.auto_enroll_new_workspaces = mode == "all"
            saved = await self._install_repo.update(conflict)
            saved.__dict__["_connector_id"] = connector.id
            await self._fan_out_state_rows(
                install=saved,
                connector_id=connector.id,
                workspace_ids=workspace_ids,
                credential_policy=defaults.credential_policy,
                enablement_source=enablement_source,
            )
            return saved
        if conflict is not None:
            conflict.auth_method = auth_method
            conflict.default_credential_policy = defaults.credential_policy
            conflict.auth_status = defaults.auth_status
            conflict.tool_citations = dict(template.tool_citation_defaults)
            conflict.static_auth_style = template.static_auth_style
            conflict.static_auth_header_name = template.static_auth_header_name
            conflict.static_auth_query_param = template.static_auth_query_param
            promoted = await self.promote_workspace_install_to_org(
                install_id=conflict.id,
                distribution=distribution,
            )
            promoted.__dict__["_connector_id"] = connector.id
            return promoted
        # Derive ``auto_enroll_new_workspaces`` from the requested distribution
        # mode rather than relying on the model's ``server_default=true``. The
        # default is right for ``mode='all'`` (admin asked for "every workspace
        # in the org") but wrong for ``selected`` / ``none``: in those cases the
        # admin has explicitly scoped the install, and letting the bootstrap
        # hook auto-enroll future workspaces would silently broaden that scope.
        auto_enroll = mode == "all"
        install = MCPConnectorInstall(
            org_id=self._org_id,
            workspace_id=None,
            install_scope="org",
            template_id=template.id,
            name=template.name,
            server_url=template.server_url,
            server_url_hash=server_url_hash(template.server_url),
            transport=template.transport,
            auth_method=auth_method,
            default_credential_policy=defaults.credential_policy,
            auth_status=defaults.auth_status,
            tool_citations=dict(template.tool_citation_defaults),
            static_auth_style=template.static_auth_style,
            static_auth_header_name=template.static_auth_header_name,
            static_auth_query_param=template.static_auth_query_param,
            auto_enroll_new_workspaces=auto_enroll,
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._install_repo.add(install)
        saved.__dict__["_connector_id"] = connector.id
        await self._fan_out_state_rows(
            install=saved,
            connector_id=connector.id,
            workspace_ids=workspace_ids,
            credential_policy=defaults.credential_policy,
            enablement_source=enablement_source,
        )
        return saved

    async def _ensure_connector_from_template(
        self,
        template: MCPConnectorTemplate,
        *,
        auth_method: str,
    ) -> MCPConnector:
        """Create or reuse the org-owned connector identity for a template."""

        repo = self._connector_repo or MCPConnectorRepository(
            self._install_repo.session,
            org_id=self._org_id,
        )
        existing = await repo.get_active_by_identity(
            template_id=template.id,
            server_url_hash=server_url_hash(template.server_url),
            slug_name=slugify_for_namespace(template.name),
        )
        if existing is not None:
            return existing
        return await repo.add(
            MCPConnector(
                org_id=self._org_id,
                template_id=template.id,
                name=template.name,
                server_url=template.server_url,
                server_url_hash=server_url_hash(template.server_url),
                transport=template.transport,
                auth_method=auth_method,
                oauth_client_config={},
                static_auth_style=template.static_auth_style,
                static_auth_header_name=template.static_auth_header_name,
                static_auth_query_param=template.static_auth_query_param,
                tool_citations=dict(template.tool_citation_defaults),
                created_by_user_id=self._actor_user_id,
            )
        )

    async def _connector_id_for_install(self, install: MCPConnectorInstall) -> str | None:
        """Best-effort connector identity lookup for compatibility install routes."""

        repo = self._connector_repo or MCPConnectorRepository(
            self._install_repo.session,
            org_id=self._org_id,
        )
        existing = await repo.get_active_by_identity(
            template_id=install.template_id,
            server_url_hash=install.server_url_hash,
            slug_name=slugify_for_namespace(install.name),
        )
        return existing.id if existing is not None else None

    async def _resolve_distribution(
        self, distribution: dict[str, Any]
    ) -> tuple[list[str], str, str]:
        """Resolve the ``distribution`` payload to a list of workspace ids.

        Validates the mode AND every requested workspace id BEFORE any
        install row is written (the caller does the actual write next),
        so a typo can't leave behind a phantom install with no state.
        Returns ``(workspace_ids, enablement_source, mode)``.
        """
        mode = distribution.get("mode")
        if mode not in {"all", "selected", "none"}:
            raise ValueError(f"unknown distribution mode: {mode!r}")

        workspace_ids: list[str] = []
        enablement_source = ""
        if mode == "all":
            if self._workspace_repo is None:
                raise RuntimeError("distribution mode='all' requires workspace_repo")
            workspaces = await self._workspace_repo.list_for_org(self._org_id)
            workspace_ids = [ws.id for ws in workspaces]
            enablement_source = "admin_auto"
        elif mode == "selected":
            raw_ids = distribution.get("workspace_ids") or []
            if not isinstance(raw_ids, list):
                raise ValueError("distribution.workspace_ids must be a list")
            requested = [str(wid) for wid in raw_ids]
            if requested:
                if self._workspace_repo is None:
                    raise RuntimeError("distribution mode='selected' requires workspace_repo")
                valid_ws = await self._workspace_repo.list_for_org(self._org_id)
                valid_ids = {ws.id for ws in valid_ws}
                unknown = [wid for wid in requested if wid not in valid_ids]
                if unknown:
                    raise ValueError("workspace_not_in_org")
            workspace_ids = requested
            enablement_source = "admin_manual"
        return workspace_ids, enablement_source, str(mode)

    async def _fan_out_state_rows(
        self,
        *,
        install: MCPConnectorInstall,
        connector_id: str,
        workspace_ids: list[str],
        credential_policy: str,
        enablement_source: str,
    ) -> None:
        """Upsert ``MCPWorkspaceConnectorState`` rows for the given workspaces.

        No-op when ``workspace_ids`` is empty (``mode='none'``).
        """
        for ws_id in workspace_ids:
            await self._state_repo.upsert_for_connector(
                workspace_id=ws_id,
                install_id=install.id,
                connector_id=connector_id,
                enabled=True,
                credential_policy=credential_policy,
                enablement_source=enablement_source,
                updated_by_user_id=self._actor_user_id,
            )

    async def create_custom_install_for_org(
        self,
        *,
        name: str,
        server_url: str,
        transport: str,
        auth_method: str,
        default_credential_policy: str,
        headers: dict[str, str] | None,
        distribution: dict[str, Any],
    ) -> MCPConnectorInstall:
        """Custom (no template) install at ``install_scope='org'``.

        Mirrors :meth:`create_from_template_for_org` but skips the
        template lookup and uses the user-supplied name / URL /
        transport. Uniqueness is enforced by the existing partial
        unique index on ``(org_id, server_url_hash)`` filtered by
        ``install_state='active'``.
        """
        workspace_ids, enablement_source, mode = await self._resolve_distribution(distribution)
        # Preflight uniqueness before insert. Without this, the partial
        # unique indexes (uq_mcp_connector_install_url_org / name_org)
        # raise IntegrityError at commit, which the route only catches
        # as a generic 500. Translate the most-common admin input
        # collision (duplicate name or URL within the org's active
        # installs) into a clean 409.
        if await self._has_install_conflict(
            server_url_hash=server_url_hash(server_url),
            name=name,
            template_id=None,
            exclude_id=None,
        ):
            raise ValueError("install_already_exists")
        defaults = install_defaults_for_auth_method(auth_method, default_credential_policy)
        auto_enroll = mode == "all"
        connector_repo = self._connector_repo or MCPConnectorRepository(
            self._install_repo.session,
            org_id=self._org_id,
        )
        connector = await connector_repo.add(
            MCPConnector(
                org_id=self._org_id,
                template_id=None,
                name=name,
                server_url=server_url,
                server_url_hash=server_url_hash(server_url),
                transport=transport,
                auth_method=auth_method,
                status="active",
                created_by_user_id=self._actor_user_id,
            )
        )
        install = MCPConnectorInstall(
            org_id=self._org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name=name,
            server_url=server_url,
            server_url_hash=server_url_hash(server_url),
            transport=transport,
            auth_method=auth_method,
            default_credential_policy=defaults.credential_policy,
            auth_status=defaults.auth_status,
            install_state="active",
            headers=dict(headers or {}),
            tools_cache=[],
            tool_citations={},
            auto_enroll_new_workspaces=auto_enroll,
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._install_repo.add(install)
        saved.__dict__["_connector_id"] = connector.id
        await self._fan_out_state_rows(
            install=saved,
            connector_id=connector.id,
            workspace_ids=workspace_ids,
            credential_policy=defaults.credential_policy,
            enablement_source=enablement_source,
        )
        return saved

    async def _has_install_conflict(
        self,
        *,
        server_url_hash: str,
        name: str,
        template_id: str | None,
        exclude_id: str | None,
    ) -> bool:
        """Return True iff an active install in this org — at any scope —
        collides on slug-normalized name, server URL, or template (R1 /
        R2 / R3 of the cross-scope uniqueness rule).

        Name match uses the canonical slug (same algorithm as the DB
        ``slug_name`` generated column) so display names that differ
        only by characters the runtime strips/replaces (``Web Tools``
        vs ``Web-Tools``) still collide. The DB has matching org-wide
        partial unique indexes, but those only surface as
        ``IntegrityError`` at commit, which the route layer can't
        reasonably translate to a precise 409. This preflight gives a
        clean ``install_already_exists`` for every creation path
        (admin custom, admin from template, workspace from template,
        promote-to-org). Any one collision is enough; ``.first()``
        avoids ``MultipleResultsFound`` when more than one column
        matches.
        """
        return (
            await self._get_install_conflict(
                server_url_hash=server_url_hash,
                name=name,
                template_id=template_id,
                exclude_id=exclude_id,
            )
            is not None
        )

    async def _get_install_conflict(
        self,
        *,
        server_url_hash: str,
        name: str,
        template_id: str | None,
        exclude_id: str | None,
    ) -> MCPConnectorInstall | None:
        from sqlalchemy import or_
        from sqlalchemy.sql import ColumnElement
        from sqlmodel import select

        from cubebox.models.mcp import MCPConnectorInstall as _Install

        or_clauses: list[ColumnElement[bool]] = [
            cast("ColumnElement[bool]", _Install.server_url_hash == server_url_hash),
            cast("ColumnElement[bool]", _Install.slug_name == slugify_for_namespace(name)),
        ]
        if template_id is not None:
            or_clauses.append(
                cast("ColumnElement[bool]", _Install.template_id == template_id),
            )
        stmt = (
            select(_Install)
            .where(
                cast("ColumnElement[bool]", _Install.org_id == self._org_id),
                cast("ColumnElement[bool]", _Install.install_state == "active"),
            )
            .where(or_(*or_clauses))
            .limit(1)
        )
        if exclude_id is not None:
            stmt = stmt.where(cast("ColumnElement[bool]", _Install.id != exclude_id))
        return (await self._install_repo.session.execute(stmt)).scalars().first()

    async def promote_workspace_install_to_org(
        self,
        *,
        install_id: str,
        distribution: dict[str, Any],
    ) -> MCPConnectorInstall:
        """Promote a workspace-scope install to org scope.

        Flips ``install_scope='org'`` + clears ``workspace_id``, then
        fans the install out into the requested distribution. The source
        workspace's existing state row is preserved untouched — it is
        explicitly excluded from the fan-out so the admin's pre-promote
        workspace policy doesn't get clobbered.

        ``auto_enroll_new_workspaces`` is set to ``True`` for
        ``mode='all'`` (admin asked for "every workspace") and ``False``
        for ``mode='selected'`` / ``'none'`` (the admin has scoped the
        install explicitly).
        """
        install = await self._install_repo.get(install_id)
        if install is None or install.org_id != self._org_id:
            raise ValueError("connector_install_not_found")
        if install.install_scope != "workspace":
            raise ValueError("install_already_org_scope")
        if install.install_state != "active":
            raise ValueError("connector_install_not_active")

        source_ws = install.workspace_id
        mode = distribution.get("mode", "none")
        # Pre-validate distribution BEFORE mutating the install row so a
        # bad workspace id rejects the whole call without leaving the
        # install in a half-promoted state.
        all_ws_ids, enablement_source, _ = await self._resolve_distribution(distribution)
        # Exclude source workspace from fan-out ONLY when its existing
        # state row should be preserved. If the source row was deleted
        # before promote, unconditional exclusion would leave the
        # workspace with no state row at all after install_scope flips
        # to 'org' and workspace_id is cleared — the effective service
        # only surfaces org installs with state rows, so the source
        # would silently lose access to the connector it used to own.
        # Look up the source row; if absent, include it in fan-out
        # so a fresh state row gets written.
        source_state = None
        if source_ws:
            source_state = await self._state_repo.get(source_ws, install_id)
        if source_state is not None:
            # Source row exists — preserve it untouched; skip from fan-out.
            all_ws_ids = [w for w in all_ws_ids if w != source_ws]
        elif source_ws:
            # Source state row was deleted before promote, but the
            # install is still install_scope='workspace' pointing at
            # source_ws. If we just flip install_scope to 'org' and
            # workspace_id to None without writing a state row for
            # source_ws, the source workspace loses the connector
            # entirely (org installs only surface to workspaces with
            # state rows). For mode='all' the source is already in
            # all_ws_ids; for 'selected' / 'none' it may not be. Force
            # the source into the fan-out so a fresh state row gets
            # written.
            if source_ws not in all_ws_ids:
                all_ws_ids = list(all_ws_ids) + [source_ws]

        # Preflight: if an active org-scope install with the same URL,
        # name, or template already exists in this org, flipping
        # workspace_id to None would violate the org partial unique
        # indexes (uq_mcp_connector_install_url_org / name / template)
        # at commit time, surfacing as IntegrityError → 500. Detect
        # the collision here so the route can return a clean 409.
        if await self._has_install_conflict(
            server_url_hash=install.server_url_hash,
            name=install.name,
            template_id=install.template_id,
            exclude_id=install_id,
        ):
            raise ValueError("install_already_exists")

        install.install_scope = "org"
        install.workspace_id = None
        install.auto_enroll_new_workspaces = mode == "all"
        saved = await self._install_repo.update(install)
        connector_id = await self._connector_id_for_install(saved)
        if connector_id is None:
            raise ValueError("connector_identity_not_found")

        if all_ws_ids:
            await self._fan_out_state_rows(
                install=saved,
                connector_id=connector_id,
                workspace_ids=all_ws_ids,
                credential_policy=saved.default_credential_policy,
                enablement_source=enablement_source,
            )
        return saved

    # ------------------------------------------------------------------ grants
    @staticmethod
    def _validate_grant_scope_shape(
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> None:
        """Re-implement the DB ``ck_mcp_credential_grants_scope_columns`` check.

        Re-implementation is deliberate: the vault write happens before
        the grant write, so if we wait for Postgres to reject a wrongly
        shaped row we've already encrypted and persisted a credential
        that nothing will ever reference. Failing here keeps the vault
        consistent. Positive assertions (not just absence of code paths)
        because "policy=user but caller passed an org-shaped tuple"
        must never silently degrade into an org-scope grant.
        """
        if grant_scope == "org":
            if workspace_id is not None or user_id is not None:
                raise ValueError("grant_scope='org' must have workspace_id=None and user_id=None")
        elif grant_scope == "workspace":
            if workspace_id is None or user_id is not None:
                raise ValueError(
                    "grant_scope='workspace' requires workspace_id and forbids user_id"
                )
        elif grant_scope == "user":
            if workspace_id is None or user_id is None:
                raise ValueError("grant_scope='user' requires both workspace_id and user_id")
        else:
            raise ValueError(f"unknown grant_scope: {grant_scope!r}")

    async def create_static_grant(
        self,
        *,
        install_id: str,
        grant_scope: str,
        plaintext: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
    ) -> MCPCredentialGrant:
        """Encrypt a static credential and bind it to an install at a scope.

        Order matters and is enforced step-by-step:

        1. **Scope-vs-FK shape validation** (re-implements the DB check
           constraint). A wrongly shaped row would be rejected by Postgres
           anyway, but only after we'd already encrypted and persisted a
           credential — so we fail fast.
        2. **Install row lookup + org match + active state**. The install
           id is a client-supplied FK; we MUST confirm it (a) exists,
           (b) belongs to this org, and (c) is still ``active`` before
           writing a credential. The repository ``get`` already filters
           on ``org_id``, so a cross-org id returns ``None`` here, but we
           still defensively re-check ``install.org_id`` in case the
           repo's filter ever regresses. The cross-org case is collapsed
           into the same ``connector_install_not_found`` ValueError as
           the truly missing case so the route layer can't be used as an
           org-existence oracle. Tombstoned installs (``install_state ==
           "uninstalled"``) raise ``connector_install_not_active`` so the
           caller can distinguish from "never existed" and surface a
           "this install was uninstalled — reinstall first" message.
        3. **Vault write + grant row**. Only reached after (1) and (2)
           pass, so a misroute can't leave behind an encrypted secret
           with no grant pointing at it.
        """
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)

        install = await self._install_repo.get(install_id)
        if install is None or install.org_id != self._org_id:
            # Cross-org and truly-missing collapse to the same error so
            # ``create_static_grant`` cannot be used to probe which ids
            # exist in other orgs.
            raise ValueError("connector_install_not_found")
        if install.install_state != "active":
            raise ValueError("connector_install_not_active")
        if install.auth_method != "static":
            # Static grants are stored as ``CREDENTIAL_KIND_MCP``; the OAuth
            # runtime branch decrypts vault rows expecting
            # ``CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN``. A static-shaped
            # grant on an OAuth (or ``auth_method='none'``) install would
            # report "valid grant" via effective-state while the runtime
            # silently kind-mismatches and skips the connector — UI says
            # connected, runs have no tool. Reject before any vault write
            # so this failure mode cannot land in the DB.
            raise ValueError("static_grant_only_valid_for_static_auth")

        connector_id = await self._connector_id_for_install(install)
        if connector_id is None:
            raise ValueError("connector_identity_not_found")
        credential_name = name or f"mcp:{install_id}:{grant_scope}"
        # Upsert (not create) so a "replace credential" flow — disconnect
        # the old grant, then submit a new token — works even if the old
        # vault row for the same (kind, name) wasn't fully cleaned up.
        # Names are deterministic per (install, scope), so a plain
        # ``create`` would collide with ``uq_credential_org_kind_name``
        # on the second attempt.
        credential_id = await self._cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name,
            plaintext=plaintext,
        )
        # Upsert the grant row too. A failed post-grant step (e.g. discovery
        # raising an unexpected error) commits the grant before surfacing as
        # 500 to the client; without this, the retry would collide with
        # ``uq_mcp_credential_grant_{org,workspace,user}``. Mirrors the OAuth
        # callback's get_for_scope → add/update pattern.
        existing = await self._grant_repo.get_for_connector_scope(
            connector_id=connector_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        if existing is None:
            grant = MCPCredentialGrant(
                org_id=self._org_id,
                install_id=install_id,
                connector_id=connector_id,
                grant_scope=grant_scope,
                workspace_id=workspace_id,
                user_id=user_id,
                credential_id=credential_id,
                grant_status="valid",
                created_by_user_id=self._actor_user_id,
            )
            return await self._grant_repo.add(grant)
        existing.install_id = install_id
        existing.connector_id = connector_id
        existing.credential_id = credential_id
        existing.refresh_credential_id = None
        existing.expires_at = None
        existing.grant_status = "valid"
        return await self._grant_repo.update(existing)

    async def disconnect_grant(
        self,
        *,
        install_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Delete the matching grant row + the vault credentials it points at.

        Per spec, disconnect is a credential-only operation: it does **not**
        touch the install row or its per-workspace state rows. OAuth-side
        revocation against the AS happens (when available) inside the
        OAuth-specific path, not here.

        Cascading the vault rows matters for two reasons:
          1. Without it, repeated "Replace credential" flows would leave
             behind orphan encrypted credentials in the vault (the next
             ``create_static_grant`` upserts the same (kind, name) row,
             but earlier rotated rows would never be reclaimed).
          2. ``cred_service.delete`` runs ``_guard_references`` so a
             credential still referenced by other grants (e.g. the
             unlikely case of two grants sharing one credential) stays
             put — we don't accidentally yank a shared row.
        """
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        deleted = await self._grant_repo.delete_scope(
            install_id,
            grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        for grant in deleted:
            for cred_id in (grant.credential_id, grant.refresh_credential_id):
                if not cred_id:
                    continue
                try:
                    await self._cred_service.delete(credential_id=cred_id)
                except Exception as exc:  # noqa: BLE001
                    # Guard fired (still referenced) or row already gone.
                    # Either way, swallow — the grant is gone, which is
                    # what disconnect promises; the orphan check on the
                    # next rotate will sweep up a stale row anyway.
                    logger.warning("MCP disconnect: skipping vault delete for {}: {}", cred_id, exc)
        # Reset discovery state when the org grant goes away. A stale
        # ``discovery_status='error'`` + ``last_error`` left over from
        # the previous credential would otherwise keep ServerErrorBanner
        # visible after disconnect AND keep the admin org-row effective
        # stuck on ``reason='discovery_failed'`` even though the grant
        # causing the failure is gone — which would hide the auth band
        # ("needs credential" form) the operator wants next. Scope:
        # only on org-grant disconnect, because ``install.discovery_status``
        # is the org-level signal; workspace/user grant freshness
        # doesn't roll up to the install row today.
        if grant_scope == "org" and deleted:
            install = await self._install_repo.get(install_id)
            if install is not None and install.discovery_status == "error":
                install.discovery_status = "not_run"
                install.last_error = None
                await self._install_repo.update(install)

    async def uninstall(self, install_id: str) -> MCPConnectorInstall:
        """Tombstone an install + cascade-clean state rows and grants.

        The install row itself is kept (``install_state='uninstalled'``)
        as an audit trail. Everything that pointed AT it is removed:

        - ``MCPWorkspaceConnectorState`` rows would otherwise become
          orphans — reinstall mints a new ``install_id``, so the old
          state rows never rebind. Worse, the admin install list (which
          joins through state rows via the workspace-effective lens)
          would surface them as ghost duplicates of the live install.
        - ``MCPCredentialGrant`` rows are tied to the previous
          (install, auth_method) pair; reusing them after reinstall
          would either be wrong (auth_method may have changed) or
          impossible (new install_id). Drop them so the operator
          provisions credentials fresh on reinstall.
        """
        install = await self._install_repo.get(install_id)
        if install is None:
            raise ValueError(f"install not found: {install_id}")
        await self._state_repo.delete_for_install(install_id)
        await self._grant_repo.delete_for_install(install_id)
        install.install_state = "uninstalled"
        install.auth_status = "disconnected"
        install.updated_at = datetime.now(UTC)
        return await self._install_repo.update(install)
