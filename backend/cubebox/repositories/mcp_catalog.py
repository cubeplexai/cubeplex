"""System-level MCP catalog repository.

The catalog table is shared across the whole deployment — there's no
``org_id`` filter and no ``ScopedRepository`` superclass. Lookups by id
and slug, plus seeder helpers (upsert + deprecate-missing).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPCatalogConnector


class MCPCatalogConnectorRepository:
    """System-level repository for ``mcp_catalog_connectors``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[MCPCatalogConnector]:
        stmt = select(MCPCatalogConnector).where(
            MCPCatalogConnector.status == "active",  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_all(self) -> list[MCPCatalogConnector]:
        stmt = select(MCPCatalogConnector)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_id(self, connector_id: str) -> MCPCatalogConnector | None:
        stmt = select(MCPCatalogConnector).where(
            MCPCatalogConnector.id == connector_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> MCPCatalogConnector | None:
        stmt = select(MCPCatalogConnector).where(
            MCPCatalogConnector.slug == slug,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

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
        default_credential_scope: str,
        oauth_dcr_supported: bool | None = None,
        oauth_default_scope: str | None = None,
        oauth_static_client_id: str | None = None,
        oauth_static_client_secret_credential_id: str | None = None,
        static_form_fields: list[dict[str, Any]] | None = None,
        static_auth_header_template: str | None = None,
        cred_metadata: dict[str, Any] | None = None,
        tool_citations: dict[str, dict[str, Any]] | None = None,
        status: str = "active",
    ) -> MCPCatalogConnector:
        """Idempotent upsert keyed by ``slug``.

        Returns the existing or newly-created row. Re-runs of the seeder
        update mutable fields without touching ``id``/``created_at``.
        """
        existing = await self.get_by_slug(slug)
        if existing is None:
            row = MCPCatalogConnector(
                slug=slug,
                name=name,
                description=description,
                provider=provider,
                server_url=server_url,
                transport=transport,
                supported_auth_methods=supported_auth_methods,
                default_credential_scope=default_credential_scope,
                oauth_dcr_supported=oauth_dcr_supported,
                oauth_default_scope=oauth_default_scope,
                oauth_static_client_id=oauth_static_client_id,
                oauth_static_client_secret_credential_id=(oauth_static_client_secret_credential_id),
                static_form_fields=static_form_fields,
                static_auth_header_template=static_auth_header_template,
                cred_metadata=cred_metadata or {},
                tool_citations=tool_citations or {},
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
        existing.default_credential_scope = default_credential_scope
        existing.oauth_dcr_supported = oauth_dcr_supported
        existing.oauth_default_scope = oauth_default_scope
        existing.oauth_static_client_id = oauth_static_client_id
        existing.oauth_static_client_secret_credential_id = oauth_static_client_secret_credential_id
        existing.static_form_fields = static_form_fields
        existing.static_auth_header_template = static_auth_header_template
        existing.cred_metadata = cred_metadata or {}
        if tool_citations is not None:
            existing.tool_citations = tool_citations
        existing.status = status
        existing.updated_at = datetime.now(UTC)
        self.session.add(existing)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing

    async def mark_deprecated_for_missing_slugs(
        self, *, kept_slugs: list[str]
    ) -> list[MCPCatalogConnector]:
        """Mark any active row whose slug isn't in ``kept_slugs`` as deprecated.

        Returns the rows that were transitioned. Idempotent: rows already in
        a non-active state are left untouched.
        """
        stmt = select(MCPCatalogConnector).where(
            MCPCatalogConnector.status == "active",  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        kept = set(kept_slugs)
        changed: list[MCPCatalogConnector] = []
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
