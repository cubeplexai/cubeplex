"""MCP connector repositories.

This module hosts both the legacy ``MCPServer`` / credential / override
repositories and the four-layer ``MCPConnectorTemplate`` /
``MCPConnectorInstall`` / ``MCPWorkspaceConnectorState`` /
``MCPCredentialGrant`` repositories.

The four new repository classes intentionally do **not** inherit
:class:`cubebox.repositories.base.ScopedRepository`. ``ScopedRepository``
requires its target model to inherit ``OrgScopedMixin`` (NOT NULL
``org_id`` AND NOT NULL ``workspace_id``), and the four-layer MCP models
deliberately allow nullable scope columns:

* ``MCPConnectorTemplate`` has no ``org_id`` at all — templates are global.
* ``MCPConnectorInstall`` has a nullable ``workspace_id`` (org-scope
  installs).
* ``MCPCredentialGrant`` has nullable ``workspace_id`` AND ``user_id``
  (a row's shape depends on ``grant_scope``).

Instead, each non-template repository uses a lightweight org-only
scoping pattern: ``__init__(session, *, org_id)``, every query filters
``org_id = self.org_id``, and ``add()`` force-sets ``org_id`` to defend
against cross-org writes. Future engineers should not try to make these
inherit ``ScopedRepository[T]`` — the scoping mismatch is structural,
not an oversight.
"""

from datetime import UTC, datetime
from types import EllipsisType
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import (
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPServer,
    MCPWorkspaceConnectorState,
    UserMCPCredential,
    WorkspaceMCPCredential,
    WorkspaceMCPOverride,
)


class MCPServerRepository:
    """Org-scoped repository for MCP server rows.

    ``org_id=None`` is reserved for the OAuth callback path: the GET
    callback runs unauthenticated, so the org is derived from the install
    referenced in the (HMAC-verified) state token. Every other call site
    MUST pass a concrete org_id.
    """

    def __init__(self, session: AsyncSession, *, org_id: str | None) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, server_id: str) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.id == server_id,  # type: ignore[arg-type]
        )
        if self.org_id is not None:
            stmt = stmt.where(MCPServer.org_id == self.org_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self,
        *,
        owner_workspace_id: str | None | EllipsisType = ...,
    ) -> list[MCPServer]:
        stmt = select(MCPServer).where(MCPServer.org_id == self.org_id)  # type: ignore[arg-type]
        if owner_workspace_id is not Ellipsis:
            stmt = stmt.where(MCPServer.owner_workspace_id == owner_workspace_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_workspace(self, workspace_id: str) -> list[MCPServer]:
        """Servers visible to ``workspace_id``.

        Combines:
        - workspace-private installs (``owner_workspace_id == workspace_id``,
          ``authed=true``)
        - org-wide installs (``owner_workspace_id IS NULL``, ``authed=true``)
          explicitly enabled by a ``workspace_mcp_overrides`` row with
          ``enabled=True`` for this workspace.
        """
        # Sub-select of enabled override server ids for this workspace.
        enabled_subq = (
            select(cast(Any, WorkspaceMCPOverride.mcp_server_id))
            .where(
                WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
                WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
                cast(Any, WorkspaceMCPOverride.enabled).is_(True),
            )
            .scalar_subquery()
        )

        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            cast(Any, MCPServer.authed).is_(True),
            or_(
                MCPServer.owner_workspace_id == workspace_id,  # type: ignore[arg-type]
                and_(
                    MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
                    cast(Any, MCPServer.id).in_(enabled_subq),
                ),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, server: MCPServer) -> MCPServer:
        if self.org_id is None:
            raise RuntimeError("MCPServerRepository.add requires a concrete org_id")
        server.org_id = self.org_id
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def update(self, server: MCPServer) -> MCPServer:
        server.updated_at = datetime.now(UTC)
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def delete(self, server_id: str) -> None:
        server = await self.get(server_id)
        if server is None:
            return
        await self.session.delete(server)
        await self.session.commit()

    async def find_by_url_hash(
        self,
        *,
        owner_workspace_id: str | None,
        server_url_hash: str,
    ) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.owner_workspace_id == owner_workspace_id,  # type: ignore[arg-type]
            MCPServer.server_url_hash == server_url_hash,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_credential_id(self, credential_id: str) -> list[MCPServer]:
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_org_wide_with_workspace_override(
        self, workspace_id: str
    ) -> list[tuple[MCPServer, WorkspaceMCPOverride | None]]:
        """Org-wide servers (owner_workspace_id IS NULL) joined with this workspace's
        override row, if any. Replaces the legacy bindings join."""
        stmt = (
            select(MCPServer, WorkspaceMCPOverride)
            .outerjoin(
                WorkspaceMCPOverride,
                (WorkspaceMCPOverride.mcp_server_id == MCPServer.id)  # type: ignore[arg-type]
                & (WorkspaceMCPOverride.workspace_id == workspace_id),
            )
            .where(
                MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
                MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
            )
        )
        rows = (await self.session.execute(stmt)).all()
        return [(srv, override) for srv, override in rows]


class WorkspaceMCPCredentialRepository:
    """Org-scoped repository for workspace MCP credentials."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
    ) -> WorkspaceMCPCredential | None:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: WorkspaceMCPCredential) -> WorkspaceMCPCredential:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get(workspace_id=workspace_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class UserMCPCredentialRepository:
    """Org-scoped repository for user MCP credentials.

    ``org_id=None`` is reserved for the OAuth callback path; the row's
    ``org_id`` is taken from the install row in that case.
    """

    def __init__(self, session: AsyncSession, *, org_id: str | None) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self,
        *,
        user_id: str,
        mcp_server_id: str,
    ) -> UserMCPCredential | None:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.user_id == user_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        if self.org_id is not None:
            stmt = stmt.where(UserMCPCredential.org_id == self.org_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: UserMCPCredential) -> UserMCPCredential:
        if self.org_id is not None:
            row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, user_id: str, mcp_server_id: str) -> None:
        row = await self.get(user_id=user_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class WorkspaceMCPOverrideRepository:
    """Org-scoped repository for workspace MCP overrides.

    A row with ``enabled=True`` makes an org-wide install visible to the
    workspace. No row = not visible (default-invisible semantics).
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get_for_workspace_and_server(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
    ) -> WorkspaceMCPOverride | None:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_workspace(self, workspace_id: str) -> list[WorkspaceMCPOverride]:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPOverride]:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
        enabled: bool,
        updated_by_user_id: str,
    ) -> WorkspaceMCPOverride:
        existing = await self.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
        )
        if existing is not None:
            existing.enabled = enabled
            existing.updated_by_user_id = updated_by_user_id
            existing.updated_at = datetime.now(UTC)
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = WorkspaceMCPOverride(
            org_id=self.org_id,
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            enabled=enabled,
            updated_by_user_id=updated_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
        )
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()


# ---------------------------------------------------------------------------
# Four-layer connector repositories.
#
# See module docstring for why these use a custom org-only scoping pattern
# instead of inheriting ``ScopedRepository[T]``.
# ---------------------------------------------------------------------------


class MCPConnectorTemplateRepository:
    """Global (un-scoped) repository for ``mcp_connector_templates``.

    Templates have no ``org_id`` — they're a deployment-wide catalog.
    Used by both admin and workspace routes; idempotent seeding lives in
    :func:`cubebox.mcp.template_seed.seed_templates`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, template_id: str) -> MCPConnectorTemplate | None:
        stmt = select(MCPConnectorTemplate).where(
            MCPConnectorTemplate.id == template_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> MCPConnectorTemplate | None:
        stmt = select(MCPConnectorTemplate).where(
            MCPConnectorTemplate.slug == slug,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active(self) -> list[MCPConnectorTemplate]:
        stmt = select(MCPConnectorTemplate).where(
            MCPConnectorTemplate.status == "active",  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_all(self) -> list[MCPConnectorTemplate]:
        stmt = select(MCPConnectorTemplate)
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert_by_slug(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        provider: str,
        server_url: str,
        transport: str,
        supported_auth_methods: list[str],
        default_credential_policy: str,
        oauth_dcr_supported: bool | None = None,
        oauth_default_scope: str | None = None,
        oauth_static_client_id: str | None = None,
        oauth_static_client_secret_credential_id: str | None = None,
        static_form_schema: list[dict[str, Any]] | None = None,
        static_auth_header_template: str | None = None,
        template_metadata: dict[str, Any] | None = None,
        tool_citation_defaults: dict[str, dict[str, Any]] | None = None,
        status: str = "active",
    ) -> MCPConnectorTemplate:
        """Idempotent upsert keyed by ``slug``.

        Returns the existing or newly-created row. Re-runs of the seeder
        update mutable fields without rotating ``id``/``created_at``.
        """
        existing = await self.get_by_slug(slug)
        if existing is None:
            row = MCPConnectorTemplate(
                slug=slug,
                name=name,
                description=description,
                provider=provider,
                server_url=server_url,
                transport=transport,
                supported_auth_methods=supported_auth_methods,
                default_credential_policy=default_credential_policy,
                oauth_dcr_supported=oauth_dcr_supported,
                oauth_default_scope=oauth_default_scope,
                oauth_static_client_id=oauth_static_client_id,
                oauth_static_client_secret_credential_id=(oauth_static_client_secret_credential_id),
                static_form_schema=static_form_schema,
                static_auth_header_template=static_auth_header_template,
                template_metadata=template_metadata or {},
                tool_citation_defaults=tool_citation_defaults or {},
                status=status,
            )
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
            return row

        existing.name = name
        existing.description = description
        existing.provider = provider
        existing.server_url = server_url
        existing.transport = transport
        existing.supported_auth_methods = supported_auth_methods
        existing.default_credential_policy = default_credential_policy
        existing.oauth_dcr_supported = oauth_dcr_supported
        existing.oauth_default_scope = oauth_default_scope
        existing.oauth_static_client_id = oauth_static_client_id
        existing.oauth_static_client_secret_credential_id = oauth_static_client_secret_credential_id
        existing.static_form_schema = static_form_schema
        existing.static_auth_header_template = static_auth_header_template
        existing.template_metadata = template_metadata or {}
        if tool_citation_defaults is not None:
            existing.tool_citation_defaults = tool_citation_defaults
        existing.status = status
        existing.updated_at = datetime.now(UTC)
        self.session.add(existing)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing

    async def mark_deprecated_for_missing_slugs(
        self, *, kept_slugs: list[str]
    ) -> list[MCPConnectorTemplate]:
        """Mark any active row whose slug isn't in ``kept_slugs`` as deprecated.

        Returns the rows that were transitioned. Idempotent: rows already
        in a non-active state are left untouched.
        """
        stmt = select(MCPConnectorTemplate).where(
            MCPConnectorTemplate.status == "active",  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        kept = set(kept_slugs)
        changed: list[MCPConnectorTemplate] = []
        for row in rows:
            if row.slug in kept:
                continue
            row.status = "deprecated"
            row.updated_at = datetime.now(UTC)
            self.session.add(row)
            changed.append(row)
        if changed:
            await self.session.flush()
            for row in changed:
                await self.session.refresh(row)
        return changed


class MCPConnectorInstallRepository:
    """Org-scoped repository for ``mcp_connector_installs``.

    ``workspace_id`` is nullable on the model (org-scope installs use
    ``workspace_id IS NULL``), so this repo cannot derive from
    :class:`ScopedRepository[T]`. It still enforces ``org_id`` on every
    query and on ``add()``.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, install_id: str) -> MCPConnectorInstall | None:
        stmt = select(MCPConnectorInstall).where(
            MCPConnectorInstall.id == install_id,  # type: ignore[arg-type]
            MCPConnectorInstall.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_org_installs(self) -> list[MCPConnectorInstall]:
        """Installs at org scope (``workspace_id IS NULL``)."""
        stmt = select(MCPConnectorInstall).where(
            MCPConnectorInstall.org_id == self.org_id,  # type: ignore[arg-type]
            MCPConnectorInstall.workspace_id.is_(None),  # type: ignore[union-attr]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_workspace_installs(self, workspace_id: str) -> list[MCPConnectorInstall]:
        """Installs owned by a specific workspace (workspace-scope only)."""
        stmt = select(MCPConnectorInstall).where(
            MCPConnectorInstall.org_id == self.org_id,  # type: ignore[arg-type]
            MCPConnectorInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, install: MCPConnectorInstall) -> MCPConnectorInstall:
        install.org_id = self.org_id
        self.session.add(install)
        await self.session.commit()
        await self.session.refresh(install)
        return install

    async def update(self, install: MCPConnectorInstall) -> MCPConnectorInstall:
        if install.org_id != self.org_id:
            raise RuntimeError(
                "MCPConnectorInstallRepository.update: install belongs to a different org"
            )
        install.updated_at = datetime.now(UTC)
        self.session.add(install)
        await self.session.commit()
        await self.session.refresh(install)
        return install


class MCPWorkspaceConnectorStateRepository:
    """Org-scoped repository for ``mcp_workspace_connector_states``.

    Workspace_id is required on this model (every row is workspace-scoped),
    but we still cannot use ``ScopedRepository[T]`` because the repository
    is constructed once per org-admin request and lists across workspaces
    (e.g. admin distribution view). Org_id is enforced; workspace_id is
    a query parameter, not constructor state.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, workspace_id: str, install_id: str) -> MCPWorkspaceConnectorState | None:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.install_id == install_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_workspace(self, workspace_id: str) -> list[MCPWorkspaceConnectorState]:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert(
        self,
        *,
        workspace_id: str,
        install_id: str,
        enabled: bool,
        credential_policy: str,
        enablement_source: str,
        updated_by_user_id: str,
    ) -> MCPWorkspaceConnectorState:
        existing = await self.get(workspace_id, install_id)
        if existing is not None:
            existing.enabled = enabled
            existing.credential_policy = credential_policy
            existing.enablement_source = enablement_source
            existing.updated_by_user_id = updated_by_user_id
            existing.updated_at = datetime.now(UTC)
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = MCPWorkspaceConnectorState(
            org_id=self.org_id,
            workspace_id=workspace_id,
            install_id=install_id,
            enabled=enabled,
            credential_policy=credential_policy,
            enablement_source=enablement_source,
            updated_by_user_id=updated_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row


class MCPCredentialGrantRepository:
    """Org-scoped repository for ``mcp_credential_grants``.

    Grants carry nullable ``workspace_id`` / ``user_id`` whose
    populated-vs-null shape is governed by ``grant_scope`` (enforced by
    DB check + partial unique indexes). The caller is responsible for
    passing the right scope-shaped values; this repo does not re-validate
    that mapping — the service layer
    (:mod:`cubebox.services.mcp_installs`) owns it. We do enforce
    ``org_id`` on every query and on ``add()``.

    **User-grant lookup note.** Per the DB check constraint, every user
    grant carries a non-null ``workspace_id`` (user grants are scoped
    per-workspace). ``get_user_grant`` therefore accepts an optional
    ``workspace_id``: when provided, the query is the exact unique key
    ``(install_id, workspace_id, user_id)``; when omitted, it returns
    the first user grant for ``(install_id, user_id)`` regardless of
    workspace, which is what most call sites want when probing "does
    this user have any grant for this install?".
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def add(self, grant: MCPCredentialGrant) -> MCPCredentialGrant:
        grant.org_id = self.org_id
        self.session.add(grant)
        await self.session.commit()
        await self.session.refresh(grant)
        return grant

    async def update(self, grant: MCPCredentialGrant) -> MCPCredentialGrant:
        """Persist edits to an existing grant row (e.g. status / expiry rotation)."""
        if grant.org_id != self.org_id:
            raise RuntimeError(
                "MCPCredentialGrantRepository.update: grant belongs to a different org"
            )
        grant.updated_at = datetime.now(UTC)
        self.session.add(grant)
        await self.session.commit()
        await self.session.refresh(grant)
        return grant

    async def get_org_grant(self, install_id: str) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.install_id == install_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "org",  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_workspace_grant(
        self, install_id: str, workspace_id: str
    ) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.install_id == install_id,  # type: ignore[arg-type]
            MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "workspace",  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_user_grant(
        self,
        install_id: str,
        user_id: str,
        *,
        workspace_id: str | None = None,
    ) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.install_id == install_id,  # type: ignore[arg-type]
            MCPCredentialGrant.user_id == user_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "user",  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            stmt = stmt.where(
                MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_scope(
        self,
        install_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.install_id == install_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == grant_scope,  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            stmt = stmt.where(
                MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        if user_id is not None:
            stmt = stmt.where(MCPCredentialGrant.user_id == user_id)  # type: ignore[arg-type]
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            await self.session.delete(row)
        if rows:
            await self.session.commit()
