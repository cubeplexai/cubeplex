"""Four-layer MCP connector repositories.

The four repository classes intentionally do **not** inherit
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
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import (
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)


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
