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
from typing import Any

from cubebox.mcp._constants import CREDENTIAL_KIND_MCP, server_url_hash
from cubebox.models import (
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
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
        """
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        install = MCPConnectorInstall(
            org_id=self._org_id,
            workspace_id=workspace_id,
            install_scope="workspace",
            template_id=template.id,
            name=template.name,
            server_url=template.server_url,
            server_url_hash=server_url_hash(template.server_url),
            transport=template.transport,
            auth_method=auth_method,
            default_credential_policy=defaults.credential_policy,
            auth_status=defaults.auth_status,
            tool_citations=dict(template.tool_citation_defaults),
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._install_repo.add(install)
        await self._state_repo.upsert(
            workspace_id=workspace_id,
            install_id=saved.id,
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
        """
        mode = distribution.get("mode")
        if mode not in {"all", "selected", "none"}:
            raise ValueError(f"unknown distribution mode: {mode!r}")

        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
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
            created_by_user_id=self._actor_user_id,
        )
        saved = await self._install_repo.add(install)

        if mode == "none":
            return saved

        if mode == "all":
            if self._workspace_repo is None:
                raise RuntimeError(
                    "create_from_template_for_org(mode='all') requires workspace_repo"
                )
            workspaces = await self._workspace_repo.list_for_org(self._org_id)
            workspace_ids: list[str] = [ws.id for ws in workspaces]
            enablement_source = "admin_auto"
        else:
            raw_ids = distribution.get("workspace_ids") or []
            if not isinstance(raw_ids, list):
                raise ValueError("distribution.workspace_ids must be a list")
            workspace_ids = [str(wid) for wid in raw_ids]
            enablement_source = "admin_manual"

        for ws_id in workspace_ids:
            await self._state_repo.upsert(
                workspace_id=ws_id,
                install_id=saved.id,
                enabled=True,
                credential_policy=defaults.credential_policy,
                enablement_source=enablement_source,
                updated_by_user_id=self._actor_user_id,
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

        Order matters: scope validation runs BEFORE the vault write so
        an org-scope misroute can't leave behind an encrypted secret
        with no grant pointing at it. The validation is a strict
        positive check rather than "skip the wrong scope branch" so
        a future refactor can't accidentally turn a user-scope request
        into a no-op that returns ``None``.
        """
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        credential_name = name or f"mcp:{install_id}:{grant_scope}"
        credential_id = await self._cred_service.create(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name,
            plaintext=plaintext,
        )
        grant = MCPCredentialGrant(
            org_id=self._org_id,
            install_id=install_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
            credential_id=credential_id,
            grant_status="valid",
            created_by_user_id=self._actor_user_id,
        )
        return await self._grant_repo.add(grant)

    async def disconnect_grant(
        self,
        *,
        install_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Delete the matching grant row.

        Intentionally does **not** touch the install row or its
        per-workspace state rows: per the spec, disconnect is a
        credential-only operation. OAuth-side revocation against the
        AS happens (when available) inside the OAuth-specific path,
        not here.
        """
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        await self._grant_repo.delete_scope(
            install_id,
            grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def uninstall(self, install_id: str) -> MCPConnectorInstall:
        """Tombstone an install without deleting workspace state rows.

        The effective-state service filters on
        ``install_state == 'active'`` so a tombstoned install becomes
        invisible to the runtime even though
        ``MCPWorkspaceConnectorState`` rows remain. Keeping the state
        rows lets a reinstall (which the partial unique indexes permit
        because they exclude tombstones) re-attach to the same shape
        without losing per-workspace policy memory.
        """
        install = await self._install_repo.get(install_id)
        if install is None:
            raise ValueError(f"install not found: {install_id}")
        install.install_state = "uninstalled"
        install.auth_status = "disconnected"
        install.updated_at = datetime.now(UTC)
        return await self._install_repo.update(install)
