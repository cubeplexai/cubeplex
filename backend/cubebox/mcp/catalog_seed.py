"""V1 MCP catalog seed.

Pure-Python list of system-level catalog templates plus an idempotent
seeder that:

1. Reads static OAuth client_id / client_secret env vars for connectors
   that don't support Dynamic Client Registration (DCR). The env var
   convention is ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID`` /
   ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_SECRET`` (slug uppercased,
   ``-`` replaced with ``_``). Missing required env vars cause a
   warning and skip the connector — the seed continues for others.
2. Upserts each entry's row into ``mcp_catalog_connectors`` keyed by
   ``slug``.
3. Marks any DB row whose slug isn't in the current ``CATALOG`` list as
   ``status='deprecated'`` (does not delete — preserves install
   references).

Invocation: ``python -m cubebox.cli seed-mcp-catalog``. Not wired into
FastAPI startup; this is intentionally an explicit deploy step.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET
from cubebox.models import Credential
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository


@dataclass(frozen=True)
class CatalogSeedEntry:
    """One row in the static v1 catalog list."""

    slug: str
    name: str
    provider: str
    description: str
    server_url: str
    transport: Literal["streamable_http", "sse"]
    supported_auth_methods: list[str]
    default_credential_scope: Literal["org", "workspace", "user", "none"]
    oauth_dcr_supported: bool | None
    oauth_default_scope: str | None
    # Env var names for connectors that need a pre-registered OAuth app
    # (DCR=False). ``None`` for DCR-supporting connectors.
    oauth_static_client_id_env: str | None
    oauth_static_client_secret_env: str | None
    static_form_fields: list[dict[str, Any]] | None
    static_auth_header_template: str | None
    cred_metadata: dict[str, Any] = field(default_factory=dict)
    tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class SeedResult:
    """Summary of one seed run."""

    upserted: int
    skipped: int
    deprecated: int
    warnings: list[str]


def _slug_to_env_prefix(slug: str) -> str:
    return f"CUBEBOX_MCP_OAUTH__{slug.upper().replace('-', '_')}"


_BEARER_TEMPLATE = "Bearer {token}"

_TOKEN_FIELD = [
    {
        "name": "token",
        "label": "API token",
        "secret": True,
        "placeholder": "Paste token",
        "helper_url": None,
    }
]

_ATLASSIAN_FIELDS = [
    {
        "name": "email",
        "label": "Email",
        "secret": False,
        "placeholder": "you@example.com",
        "helper_url": None,
    },
    {
        "name": "api_token",
        "label": "API token",
        "secret": True,
        "placeholder": "Paste API token",
        "helper_url": "https://id.atlassian.com/manage-profile/security/api-tokens",
    },
]


CATALOG: list[CatalogSeedEntry] = [
    CatalogSeedEntry(
        slug="github",
        name="GitHub",
        provider="GitHub",
        description="GitHub MCP server: repos, issues, pull requests, code search.",
        server_url="https://api.githubcopilot.com/mcp/",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=False,
        oauth_default_scope="repo read:user",
        oauth_static_client_id_env=f"{_slug_to_env_prefix('github')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('github')}__CLIENT_SECRET",
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={"docs_url": "https://docs.github.com/en/copilot"},
    ),
    CatalogSeedEntry(
        slug="notion",
        name="Notion",
        provider="Notion",
        description="Notion MCP server: pages, databases, search across your workspace.",
        server_url="https://mcp.notion.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={"docs_url": "https://developers.notion.com/"},
    ),
    CatalogSeedEntry(
        slug="linear",
        name="Linear",
        provider="Linear",
        description="Linear MCP server: issues, projects, cycles, teams.",
        server_url="https://mcp.linear.app/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={"docs_url": "https://linear.app/developers"},
    ),
    CatalogSeedEntry(
        slug="atlassian",
        name="Atlassian",
        provider="Atlassian",
        description="Atlassian MCP server: Jira and Confluence.",
        server_url="https://mcp.atlassian.com/v1/mcp/authv2",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_ATLASSIAN_FIELDS,
        static_auth_header_template="Basic {b64(email:api_token)}",
        cred_metadata={"docs_url": "https://developer.atlassian.com/"},
    ),
    CatalogSeedEntry(
        slug="asana",
        name="Asana",
        provider="Asana",
        description="Asana MCP server: tasks, projects, teams.",
        server_url="https://mcp.asana.com/v2/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={"docs_url": "https://developers.asana.com/"},
    ),
    CatalogSeedEntry(
        slug="slack",
        name="Slack",
        provider="Slack",
        description="Slack MCP server: channels, messages, search.",
        server_url="https://slack.com/api/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=False,
        oauth_default_scope="channels:read chat:write users:read",
        oauth_static_client_id_env=f"{_slug_to_env_prefix('slack')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('slack')}__CLIENT_SECRET",
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://api.slack.com/"},
    ),
    CatalogSeedEntry(
        slug="cloudflare-workers",
        name="Cloudflare Workers",
        provider="Cloudflare",
        description="Cloudflare Workers MCP server: deploy and inspect Workers.",
        server_url="https://workers.mcp.cloudflare.com/sse",
        transport="sse",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://developers.cloudflare.com/workers/"},
    ),
    CatalogSeedEntry(
        slug="cloudflare-logs",
        name="Cloudflare Logs",
        provider="Cloudflare",
        description="Cloudflare Logs MCP server: query logs and analytics.",
        server_url="https://logs.mcp.cloudflare.com/sse",
        transport="sse",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://developers.cloudflare.com/logs/"},
    ),
    CatalogSeedEntry(
        slug="cloudflare-radar",
        name="Cloudflare Radar",
        provider="Cloudflare",
        description="Cloudflare Radar MCP server: internet traffic and threat intel.",
        server_url="https://radar.mcp.cloudflare.com/sse",
        transport="sse",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://radar.cloudflare.com/"},
    ),
    CatalogSeedEntry(
        slug="sentry",
        name="Sentry",
        provider="Sentry",
        description="Sentry MCP server: issues, events, releases.",
        server_url="https://mcp.sentry.dev/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={"docs_url": "https://docs.sentry.io/"},
    ),
    CatalogSeedEntry(
        slug="intercom",
        name="Intercom",
        provider="Intercom",
        description="Intercom MCP server: conversations, contacts, articles.",
        server_url="https://mcp.intercom.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://developers.intercom.com/"},
    ),
    CatalogSeedEntry(
        slug="gws",
        name="Google Workspace",
        provider="Google Workspace",
        description="Google Workspace MCP server: Gmail, Drive, Calendar, Docs.",
        server_url="https://www.googleapis.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_scope="org",
        oauth_dcr_supported=False,
        oauth_default_scope=(
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/drive.readonly"
        ),
        oauth_static_client_id_env=f"{_slug_to_env_prefix('gws')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('gws')}__CLIENT_SECRET",
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://developers.google.com/workspace"},
    ),
    CatalogSeedEntry(
        slug="mslearn",
        name="Microsoft Learn",
        provider="Microsoft",
        description="Microsoft Learn MCP server: public docs search, no auth required.",
        server_url="https://learn.microsoft.com/api/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_scope="none",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=None,
        static_auth_header_template=None,
        cred_metadata={"docs_url": "https://learn.microsoft.com/"},
    ),
    CatalogSeedEntry(
        slug="webtools",
        name="WebTools",
        provider="Cubebox",
        description="Self-hosted WebTools MCP server: web_search and web_fetch.",
        server_url="http://localhost:8020/api/webtools",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_scope="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_fields=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        cred_metadata={},
    ),
]


def _credential_name_for_slug(slug: str) -> str:
    return f"mcp-oauth-{slug}-secret"


async def _upsert_oauth_client_secret(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    slug: str,
    plaintext: str,
) -> str:
    """Upsert the system-level Credential row for a connector's OAuth app secret.

    System credentials live with ``org_id=NULL``; uniqueness on (kind, name)
    is enforced by ``uq_credential_system_kind_name``.
    """
    name = _credential_name_for_slug(slug)
    stmt = select(Credential).where(
        Credential.org_id.is_(None),  # type: ignore[union-attr]
        Credential.kind == CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,  # type: ignore[arg-type]
        Credential.name == name,  # type: ignore[arg-type]
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    ciphertext = await backend.encrypt(plaintext.encode("utf-8"))
    if existing is None:
        row = Credential(
            org_id=None,
            kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
            name=name,
            value_encrypted=ciphertext,
            cred_metadata={"slug": slug},
            created_by_user_id=None,
        )
        session.add(row)
        await session.flush()
        return row.id
    existing.value_encrypted = ciphertext
    session.add(existing)
    await session.flush()
    return existing.id


async def seed_catalog(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    get_env: Callable[[str], str | None] = os.getenv,
    catalog: list[CatalogSeedEntry] | None = None,
) -> SeedResult:
    """Idempotent: upsert every catalog entry into the DB.

    For connectors that need a pre-registered OAuth client (DCR=False, e.g.
    GitHub / Slack / Google Workspace), reads the static client id / secret
    from environment variables, encrypts the secret into a system-level
    credential row, and links it on the catalog row. Missing env vars cause
    that single connector to be skipped with a warning — others continue.

    Returns a ``SeedResult`` summarizing the run. Run twice → no diffs.
    """
    repo = MCPCatalogConnectorRepository(session)
    entries = catalog if catalog is not None else CATALOG

    upserted = 0
    skipped = 0
    warnings: list[str] = []
    active_slugs: list[str] = []

    for entry in entries:
        client_id: str | None = None
        client_secret_credential_id: str | None = None

        if entry.oauth_static_client_secret_env is not None:
            id_env = entry.oauth_static_client_id_env
            secret_env = entry.oauth_static_client_secret_env
            assert id_env is not None  # always paired with secret env
            raw_id = (get_env(id_env) or "").strip()
            raw_secret = (get_env(secret_env) or "").strip()
            if not raw_id or not raw_secret:
                msg = f"Skipping catalog connector '{entry.slug}': missing {id_env} or {secret_env}"
                logger.warning(msg)
                warnings.append(msg)
                skipped += 1
                active_slugs.append(entry.slug)
                continue
            client_id = raw_id
            client_secret_credential_id = await _upsert_oauth_client_secret(
                session, backend, slug=entry.slug, plaintext=raw_secret
            )

        await repo.upsert_by_slug(
            slug=entry.slug,
            name=entry.name,
            description=entry.description,
            provider=entry.provider,
            server_url=entry.server_url,
            transport=entry.transport,
            supported_auth_methods=list(entry.supported_auth_methods),
            default_credential_scope=entry.default_credential_scope,
            oauth_dcr_supported=entry.oauth_dcr_supported,
            oauth_default_scope=entry.oauth_default_scope,
            oauth_static_client_id=client_id,
            oauth_static_client_secret_credential_id=client_secret_credential_id,
            static_form_fields=entry.static_form_fields,
            static_auth_header_template=entry.static_auth_header_template,
            cred_metadata=dict(entry.cred_metadata),
            tool_citations=dict(entry.tool_citations),
            status="active",
        )
        upserted += 1
        active_slugs.append(entry.slug)

    deprecated_rows = await repo.mark_deprecated_for_missing_slugs(kept_slugs=active_slugs)

    # Flush any pending Credential inserts/updates so caller sees a
    # consistent in-session state. Commit/rollback is the caller's
    # responsibility — the CLI owns the transaction boundary so
    # ``--dry-run`` can roll back the whole operation.
    await session.flush()

    return SeedResult(
        upserted=upserted,
        skipped=skipped,
        deprecated=len(deprecated_rows),
        warnings=warnings,
    )
