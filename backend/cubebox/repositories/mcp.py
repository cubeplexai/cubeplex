"""Four-layer MCP connector repositories.

The four repository classes intentionally do **not** inherit
:class:`cubebox.repositories.base.ScopedRepository`. ``ScopedRepository``
requires its target model to inherit ``OrgScopedMixin`` (NOT NULL
``org_id`` AND NOT NULL ``workspace_id``), and the four-layer MCP models
deliberately allow nullable scope columns:

* ``MCPConnectorTemplate`` has no ``org_id`` at all — templates are global.
* ``MCPConnector`` is org-scoped but deliberately has no workspace_id;
  workspace enablement lives in ``MCPWorkspaceConnectorState``.
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
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from cubebox.mcp._constants import server_url_hash, slugify_for_namespace
from cubebox.models import (
    MCPConnector,
    MCPConnectorTemplate,
    MCPConnectorTemplateSettings,
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
        static_auth_style: str = "bearer",
        static_auth_header_name: str | None = None,
        static_auth_query_param: str | None = None,
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
                static_auth_style=static_auth_style,
                static_auth_header_name=static_auth_header_name,
                static_auth_query_param=static_auth_query_param,
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
        existing.static_auth_style = static_auth_style
        existing.static_auth_header_name = static_auth_header_name
        existing.static_auth_query_param = static_auth_query_param
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

    async def list_visible_for_org(self, org_id: str) -> list[MCPConnectorTemplate]:
        """All active templates visible to an org: global + any owned by the org
        (both org-scoped and workspace-scoped rows count — the workspace ones are
        the org's own custom connectors, not foreign-org data)."""
        stmt = select(MCPConnectorTemplate).where(
            cast("ColumnElement[bool]", MCPConnectorTemplate.status == "active"),
            or_(
                cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "global"),
                cast("ColumnElement[bool]", MCPConnectorTemplate.org_id == org_id),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_visible_for_workspace(
        self, org_id: str, workspace_id: str
    ) -> list[MCPConnectorTemplate]:
        """Active templates visible inside a specific workspace.

        Includes: global templates; org-scoped templates for this org;
        workspace-scoped templates whose workspace_id matches exactly.
        Workspace-scoped templates from sibling workspaces are excluded.
        """
        stmt = select(MCPConnectorTemplate).where(
            cast("ColumnElement[bool]", MCPConnectorTemplate.status == "active"),
            or_(
                cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "global"),
                and_(
                    cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "org"),
                    cast("ColumnElement[bool]", MCPConnectorTemplate.org_id == org_id),
                ),
                and_(
                    cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "workspace"),
                    cast("ColumnElement[bool]", MCPConnectorTemplate.workspace_id == workspace_id),
                ),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_scoped(
        self,
        *,
        scope: str,
        org_id: str,
        workspace_id: str | None,
        created_by_user_id: str,
        name: str,
        server_url: str,
        transport: str,
        supported_auth_methods: list[str],
        default_credential_policy: str,
    ) -> MCPConnectorTemplate:
        """Create a custom org- or workspace-scoped connector template.

        Slug is generated as ``custom-<slugified-name>-<last-6-of-org_id>``.
        Raises ``ValueError("connector_name_conflict")`` if the slug already exists.
        """
        name_slug = slugify_for_namespace(name)
        slug = f"custom-{name_slug}-{org_id[-6:]}"
        existing = await self.get_by_slug(slug)
        if existing is not None:
            raise ValueError("connector_name_conflict")
        row = MCPConnectorTemplate(
            slug=slug,
            name=name,
            description="",
            provider="custom",
            server_url=server_url,
            transport=transport,
            supported_auth_methods=supported_auth_methods,
            default_credential_policy=default_credential_policy,
            scope=scope,
            org_id=org_id,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            status="active",
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            raise ValueError("connector_name_conflict") from None
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def promote_to_org(self, template_id: str) -> MCPConnectorTemplate:
        """Promote a workspace-scoped template to org scope.

        Clears ``workspace_id`` and sets ``scope='org'``.
        Raises ``ValueError("template_not_owned_by_workspace")`` if the template
        is not currently workspace-scoped.
        """
        row = await self.get(template_id)
        if row is None or row.scope != "workspace":
            raise ValueError("template_not_owned_by_workspace")
        row.scope = "org"
        row.workspace_id = None
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row


class MCPTemplateSettingsRepository:
    """Org-scoped per-(org, template) settings.

    Absence of a row means all defaults apply (spec §3.4). The ``org_id``
    constructor argument is force-set on every write to defend against
    cross-org mutations.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, template_id: str) -> MCPConnectorTemplateSettings | None:
        stmt = select(MCPConnectorTemplateSettings).where(
            cast("ColumnElement[bool]", MCPConnectorTemplateSettings.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnectorTemplateSettings.template_id == template_id),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_disabled(
        self,
        template_id: str,
        disabled: bool,
        *,
        updated_by_user_id: str | None,
    ) -> MCPConnectorTemplateSettings:
        """Upsert the disabled flag for a (org, template) pair.

        One row per (org, template); re-calling is idempotent — same row id.
        """
        existing = await self.get(template_id)
        if existing is not None:
            existing.disabled = disabled
            existing.updated_by_user_id = updated_by_user_id
            existing.updated_at = datetime.now(UTC)
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = MCPConnectorTemplateSettings(
            org_id=self.org_id,
            template_id=template_id,
            disabled=disabled,
            updated_by_user_id=updated_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def disabled_template_ids(self) -> set[str]:
        """IDs of templates that have an explicit disabled=True setting for this org."""
        stmt = select(MCPConnectorTemplateSettings).where(
            cast("ColumnElement[bool]", MCPConnectorTemplateSettings.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnectorTemplateSettings.disabled == True),  # noqa: E712
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return {row.template_id for row in rows}


class MCPConnectorRepository:
    """Org-scoped repository for connector identity rows."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, connector_id: str) -> MCPConnector | None:
        stmt = select(MCPConnector).where(
            cast("ColumnElement[bool]", MCPConnector.id == connector_id),
            cast("ColumnElement[bool]", MCPConnector.org_id == self.org_id),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_template_id(self, template_id: str) -> MCPConnector | None:
        """Return the active connector for this org that was created from ``template_id``."""
        stmt = select(MCPConnector).where(
            cast("ColumnElement[bool]", MCPConnector.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnector.template_id == template_id),
            cast("ColumnElement[bool]", MCPConnector.status == "active"),
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_or_create_for_template(
        self,
        template: MCPConnectorTemplate,
        *,
        created_by_user_id: str,
    ) -> MCPConnector:
        """Lazily create an org connector from a template snapshot.

        Race-safe: on ``IntegrityError`` (concurrent create won the race),
        roll back and re-fetch the row that the winner inserted.
        """
        existing = await self.get_by_template_id(template.id)
        if existing is not None:
            return existing
        row = MCPConnector(
            org_id=self.org_id,
            template_id=template.id,
            name=template.name,
            slug_name=slugify_for_namespace(template.name),
            server_url=template.server_url,
            server_url_hash=server_url_hash(template.server_url),
            transport=template.transport,
            default_credential_policy=template.default_credential_policy,
            static_auth_style=template.static_auth_style,
            static_auth_header_name=template.static_auth_header_name,
            static_auth_query_param=template.static_auth_query_param,
            tool_citations=dict(template.tool_citation_defaults),
            created_by_user_id=created_by_user_id,
        )
        try:
            return await self.add(row)
        except IntegrityError:
            await self.session.rollback()
            raced = await self.get_by_template_id(template.id)
            if raced is None:
                raise
            return raced

    async def get_active_by_identity(
        self,
        *,
        template_id: str | None,
        server_url_hash: str,
        slug_name: str,
    ) -> MCPConnector | None:
        identity_matches: list[ColumnElement[bool]] = [
            cast("ColumnElement[bool]", MCPConnector.server_url_hash == server_url_hash),
            cast("ColumnElement[bool]", MCPConnector.slug_name == slug_name),
        ]
        if template_id is not None:
            identity_matches.append(
                cast("ColumnElement[bool]", MCPConnector.template_id == template_id)
            )

        stmt = select(MCPConnector).where(
            cast("ColumnElement[bool]", MCPConnector.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnector.status == "active"),
            or_(*identity_matches),
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def list_active(self) -> list[MCPConnector]:
        stmt = select(MCPConnector).where(
            cast("ColumnElement[bool]", MCPConnector.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnector.status == "active"),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_auto_enroll_active(self) -> list[MCPConnector]:
        stmt = select(MCPConnector).where(
            cast("ColumnElement[bool]", MCPConnector.org_id == self.org_id),
            cast("ColumnElement[bool]", MCPConnector.status == "active"),
            cast("ColumnElement[bool]", MCPConnector.auto_enroll_new_workspaces == True),  # noqa: E712
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_org_installs(self) -> list[MCPConnector]:
        return await self.list_active()

    async def get_connector_id_for_install(self, connector: MCPConnector) -> str | None:
        return connector.id

    async def add(self, connector: MCPConnector) -> MCPConnector:
        connector.org_id = self.org_id
        self.session.add(connector)
        await self.session.commit()
        await self.session.refresh(connector)
        return connector

    async def update(self, connector: MCPConnector) -> MCPConnector:
        if connector.org_id != self.org_id:
            raise RuntimeError(
                "MCPConnectorRepository.update: connector belongs to a different org"
            )
        connector.updated_at = datetime.now(UTC)
        self.session.add(connector)
        await self.session.commit()
        await self.session.refresh(connector)
        return connector


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

    async def get_by_connector(
        self,
        workspace_id: str,
        connector_id: str,
    ) -> MCPWorkspaceConnectorState | None:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get(
        self,
        workspace_id: str,
        connector_id: str,
    ) -> MCPWorkspaceConnectorState | None:
        return await self.get_by_connector(workspace_id, connector_id)

    async def list_for_workspace(self, workspace_id: str) -> list[MCPWorkspaceConnectorState]:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_connector(self, connector_id: str) -> list[MCPWorkspaceConnectorState]:
        """Every state row pointing at this connector across all workspaces."""
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_install(self, connector_id: str) -> list[MCPWorkspaceConnectorState]:
        return await self.list_for_connector(connector_id)

    async def delete_for_connector(self, connector_id: str, *, flush_only: bool = False) -> int:
        """Bulk-delete every state row for ``connector_id``. Returns count.

        When ``flush_only=True``, flushes deletes to the DB without committing
        so that the caller can include them in a larger atomic transaction.
        """
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            await self.session.delete(row)
        if rows:
            if flush_only:
                await self.session.flush()
            else:
                await self.session.commit()
        return len(rows)

    async def delete_for_install(self, connector_id: str) -> int:
        return await self.delete_for_connector(connector_id)

    async def upsert_for_connector(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        enabled: bool,
        credential_policy: str,
        enablement_source: str,
        updated_by_user_id: str,
    ) -> MCPWorkspaceConnectorState:
        existing = await self.get_by_connector(workspace_id, connector_id)
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
            connector_id=connector_id,
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
    per-workspace). ``get_user_grant_for_connector`` therefore requires
    the exact unique key ``(connector_id, workspace_id, user_id)``.
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

    async def get_org_grant_for_connector(
        self,
        connector_id: str,
    ) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "org",  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_org_grant(self, connector_id: str) -> MCPCredentialGrant | None:
        return await self.get_org_grant_for_connector(connector_id)

    async def has_any_grant_for_connector(self, connector_id: str) -> bool:
        """True if any grant (any scope) exists for this connector.

        Used by auth-method-switch to refuse changes that would orphan
        credentials provisioned for the previous method.
        """
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def has_any_grant(self, connector_id: str) -> bool:
        return await self.has_any_grant_for_connector(connector_id)

    async def get_workspace_grant_for_connector(
        self,
        connector_id: str,
        workspace_id: str,
    ) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
            MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "workspace",  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_user_grant_for_connector(
        self,
        connector_id: str,
        user_id: str,
        *,
        workspace_id: str,
    ) -> MCPCredentialGrant | None:
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
            MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPCredentialGrant.user_id == user_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == "user",  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_for_connector_scope(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> MCPCredentialGrant | None:
        """Single grant per (connector, scope-shape)."""
        if grant_scope == "org":
            return await self.get_org_grant_for_connector(connector_id)
        if grant_scope == "workspace":
            assert workspace_id is not None, "workspace grant requires workspace_id"
            return await self.get_workspace_grant_for_connector(connector_id, workspace_id)
        assert workspace_id is not None and user_id is not None, "user grant requires both"
        return await self.get_user_grant_for_connector(
            connector_id,
            user_id,
            workspace_id=workspace_id,
        )

    async def delete_for_connector(self, connector_id: str, *, flush_only: bool = False) -> int:
        """Bulk-delete every grant for ``connector_id``. Returns count.

        When ``flush_only=True``, flushes deletes to the DB without committing
        so that the caller can include them in a larger atomic transaction.
        """
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            await self.session.delete(row)
        if rows:
            if flush_only:
                await self.session.flush()
            else:
                await self.session.commit()
        return len(rows)

    async def delete_scope(
        self,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> list[MCPCredentialGrant]:
        """Delete matching grants. Returns the deleted rows so callers can
        clean up the credentials they pointed at (the vault rows aren't
        scoped to grants by FK; the service is responsible for cascading)."""
        stmt = select(MCPCredentialGrant).where(
            MCPCredentialGrant.org_id == self.org_id,  # type: ignore[arg-type]
            MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
            MCPCredentialGrant.grant_scope == grant_scope,  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            stmt = stmt.where(
                MCPCredentialGrant.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        if user_id is not None:
            stmt = stmt.where(MCPCredentialGrant.user_id == user_id)  # type: ignore[arg-type]
        rows = list((await self.session.execute(stmt)).scalars().all())
        deleted = list(rows)
        for row in rows:
            await self.session.delete(row)
        if rows:
            await self.session.commit()
        return deleted
