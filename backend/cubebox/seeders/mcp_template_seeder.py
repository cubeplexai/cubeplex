"""MCP connector template seeder: upserts the v1 templates at startup.

Multi-replica safe via Redis named lock (same pattern as skill_seeder).

Until plan Task 9 retires the legacy four-table model (``mcp_servers`` +
``mcp_catalog_connectors`` + per-workspace credential rows), this seeder
performs a **dual write**: every entry in the v1 template CATALOG goes
into BOTH the new ``mcp_connector_templates`` table (via
:class:`MCPConnectorTemplateRepository`) AND the legacy
``mcp_catalog_connectors`` table (via the legacy
:class:`MCPCatalogConnectorRepository`).

This is required because the legacy admin/member catalog routes
(``GET /api/v1/admin/mcp/catalog`` and ``GET /api/v1/ws/{ws}/mcp/catalog``)
still read from ``mcp_catalog_connectors``. Dropping the legacy half of
the write would leave fresh deployments with an empty legacy catalog
even though the data exists on the new side. The dual write is removed
together with the legacy table + routes in the follow-up cleanup PR.
"""

from __future__ import annotations

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend

LOCK_KEY = "cubebox:lock:mcp_template_seeder"
LOCK_TTL_SECONDS = 60


async def _seed_legacy_catalog_from_templates(
    session: AsyncSession,
    backend: EncryptionBackend,
) -> tuple[int, int, list[str]]:
    """Mirror the v1 template CATALOG into the legacy ``mcp_catalog_connectors``.

    Reuses the same env-var resolution as the new seeder for static
    OAuth client_id / client_secret values (the credential row written
    by the new seeder's ``_upsert_oauth_client_secret`` is reused here
    so we don't double-encrypt). Returns ``(upserted, skipped,
    warnings)``; the legacy ``deprecated`` count is logged but not
    returned because the new-table count is the source of truth for
    the lifespan log line.

    NOTE: this function is local to the dual-write transition. It will
    be deleted together with the legacy ``mcp_catalog_connectors``
    table and its routes in the follow-up cleanup PR.
    """
    import os

    from sqlalchemy import select

    from cubebox.mcp._constants import CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET
    from cubebox.mcp.template_seed import CATALOG, _credential_name_for_slug
    from cubebox.models import Credential
    from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

    legacy_repo = MCPCatalogConnectorRepository(session)
    upserted = 0
    skipped = 0
    warnings: list[str] = []
    active_slugs: list[str] = []

    for entry in CATALOG:
        client_id: str | None = None
        client_secret_credential_id: str | None = None

        if entry.oauth_static_client_secret_env is not None:
            id_env = entry.oauth_static_client_id_env
            secret_env = entry.oauth_static_client_secret_env
            assert id_env is not None
            raw_id = (os.getenv(id_env) or "").strip()
            raw_secret = (os.getenv(secret_env) or "").strip()
            if not raw_id or not raw_secret:
                # The new-table seeder already logged this skip; keep
                # the legacy side in sync (omit the row entirely) and
                # let it be marked deprecated on the next pass if the
                # env var ever returns.
                msg = f"legacy catalog skip '{entry.slug}': missing {id_env} or {secret_env}"
                warnings.append(msg)
                skipped += 1
                active_slugs.append(entry.slug)
                continue
            client_id = raw_id
            # Reuse the system credential row written by the new seeder
            # (same name/kind keying). If it isn't present for some
            # reason, fall through to the legacy upsert without the
            # credential FK — the legacy route surfaces the connector
            # but installs against it will fail at OAuth time, which
            # matches today's behavior on a half-seeded deployment.
            name = _credential_name_for_slug(entry.slug)
            stmt = select(Credential).where(
                Credential.org_id.is_(None),  # type: ignore[union-attr]
                Credential.kind == CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,  # type: ignore[arg-type]
                Credential.name == name,  # type: ignore[arg-type]
            )
            existing_cred = (await session.execute(stmt)).scalar_one_or_none()
            if existing_cred is not None:
                client_secret_credential_id = existing_cred.id

        await legacy_repo.upsert_by_slug(
            slug=entry.slug,
            name=entry.name,
            description=entry.description,
            provider=entry.provider,
            server_url=entry.server_url,
            transport=entry.transport,
            supported_auth_methods=list(entry.supported_auth_methods),
            # Legacy column is ``default_credential_scope`` (renamed to
            # ``default_credential_policy`` on the new table). Same values.
            default_credential_scope=entry.default_credential_policy,
            oauth_dcr_supported=entry.oauth_dcr_supported,
            oauth_default_scope=entry.oauth_default_scope,
            oauth_static_client_id=client_id,
            oauth_static_client_secret_credential_id=client_secret_credential_id,
            # Legacy column is ``static_form_fields`` (renamed to
            # ``static_form_schema`` on the new table). Same values.
            static_form_fields=entry.static_form_schema,
            static_auth_header_template=entry.static_auth_header_template,
            # Legacy columns: ``cred_metadata`` / ``tool_citations``.
            cred_metadata=dict(entry.template_metadata),
            tool_citations=dict(entry.tool_citation_defaults),
            status="active",
        )
        upserted += 1
        active_slugs.append(entry.slug)

    deprecated_rows = await legacy_repo.mark_deprecated_for_missing_slugs(kept_slugs=active_slugs)
    if deprecated_rows:
        logger.info("MCP legacy catalog seed: deprecated={} rows", len(deprecated_rows))
    return upserted, skipped, warnings


async def seed_mcp_templates(
    *,
    db_session: AsyncSession,
    backend: EncryptionBackend,
    redis: Redis,
) -> None:
    """Idempotently seed the MCP connector template catalog into the database.

    Dual-writes the same entries into the legacy ``mcp_catalog_connectors``
    table — see the module docstring for why. Multi-replica safe: only
    one process holding the Redis lock runs the seed; others log and
    return.
    """
    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("MCP template seeder: lock held by another replica; skipping this run")
        return

    try:
        from cubebox.mcp.template_seed import seed_templates

        result = await seed_templates(db_session, backend)
        # Dual-write into the legacy table while it (and its routes) are
        # still live. Removed in the cleanup PR alongside the legacy
        # model + routes.
        (
            legacy_upserted,
            legacy_skipped,
            legacy_warnings,
        ) = await _seed_legacy_catalog_from_templates(db_session, backend)
        await db_session.commit()
        logger.info(
            "MCP template seed: upserted={} skipped={} deprecated={}",
            result.upserted,
            result.skipped,
            result.deprecated,
        )
        logger.info(
            "MCP legacy catalog dual-write: upserted={} skipped={}",
            legacy_upserted,
            legacy_skipped,
        )
        for warning in result.warnings:
            logger.warning("MCP template seed: {}", warning)
        for warning in legacy_warnings:
            logger.warning("MCP legacy catalog seed: {}", warning)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass


# Backwards-compatible alias. Both names invoke the dual-write seeder so
# the legacy ``mcp_catalog_connectors`` table stays populated until the
# follow-up cleanup PR drops it. ``app.lifespan`` calls ``seed_mcp_catalog``
# for historical reasons; ``cubebox.cli.seed_mcp_templates`` and any new
# call site should prefer ``seed_mcp_templates``.
seed_mcp_catalog = seed_mcp_templates
