"""V1 MCP connector template seed.

Pure-Python list of system-level connector templates plus an idempotent
seeder that:

1. Reads static OAuth client_id / client_secret env vars for connectors
   that don't support Dynamic Client Registration (DCR). The env var
   convention is ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID`` /
   ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_SECRET`` (slug uppercased,
   ``-`` replaced with ``_``). Missing required env vars cause a
   warning and skip the connector — the seed continues for others.
2. Upserts each entry's row into ``mcp_connector_templates`` keyed by
   ``slug``.
3. Marks any DB row whose slug isn't in the current ``CATALOG`` list as
   ``status='deprecated'`` (does not delete — preserves install
   references).

Invocation: ``python -m cubebox.cli seed-mcp-templates``. Not wired into
FastAPI startup; this is intentionally an explicit deploy step.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
    server_url_hash,
)
from cubebox.models import Credential, MCPConnectorInstall
from cubebox.repositories.mcp import MCPConnectorTemplateRepository


@dataclass(frozen=True)
class MCPConnectorTemplateSeedEntry:
    """One row in the static v1 connector template list."""

    slug: str
    name: str
    provider: str
    description: str
    server_url: str
    transport: Literal["streamable_http", "sse"]
    supported_auth_methods: list[str]
    default_credential_policy: Literal["org", "workspace", "user", "none"]
    oauth_dcr_supported: bool | None
    oauth_default_scope: str | None
    # Env var names for connectors that need a pre-registered OAuth app
    # (DCR=False). ``None`` for DCR-supporting connectors.
    oauth_static_client_id_env: str | None
    oauth_static_client_secret_env: str | None
    static_form_schema: list[dict[str, Any]] | None
    static_auth_header_template: str | None
    # Runtime auth shape for the ``static`` branch. Default ``bearer`` keeps
    # existing connectors on ``Authorization: Bearer <key>``. Search MCPs
    # diverge: Exa wants ``header`` with ``x-api-key``; some servers want
    # ``query`` with a URL param like ``tavilyApiKey``.
    static_auth_style: Literal["bearer", "header", "query"] = "bearer"
    static_auth_header_name: str | None = None
    static_auth_query_param: str | None = None
    template_metadata: dict[str, Any] = field(default_factory=dict)
    tool_citation_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)


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


CATALOG: list[MCPConnectorTemplateSeedEntry] = [
    MCPConnectorTemplateSeedEntry(
        slug="github",
        name="GitHub",
        provider="GitHub",
        description="GitHub MCP server: repos, issues, pull requests, code search.",
        server_url="https://api.githubcopilot.com/mcp/",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=False,
        oauth_default_scope="repo read:user",
        oauth_static_client_id_env=f"{_slug_to_env_prefix('github')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('github')}__CLIENT_SECRET",
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://docs.github.com/en/copilot"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="notion",
        name="Notion",
        provider="Notion",
        description="Notion MCP server: pages, databases, search across your workspace.",
        server_url="https://mcp.notion.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://developers.notion.com/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="linear",
        name="Linear",
        provider="Linear",
        description="Linear MCP server: issues, projects, cycles, teams.",
        server_url="https://mcp.linear.app/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://linear.app/developers"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="atlassian",
        name="Atlassian",
        provider="Atlassian",
        description="Atlassian Rovo MCP server: Jira, Confluence, Bitbucket, and Compass.",
        server_url="https://mcp.atlassian.com/v1/mcp/authv2",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_ATLASSIAN_FIELDS,
        static_auth_header_template="Basic {b64(email:api_token)}",
        template_metadata={
            "docs_url": (
                "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/"
                "getting-started-with-the-atlassian-remote-mcp-server/"
            )
        },
    ),
    MCPConnectorTemplateSeedEntry(
        slug="asana",
        name="Asana",
        provider="Asana",
        description="Asana MCP server: tasks, projects, teams.",
        server_url="https://mcp.asana.com/v2/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://developers.asana.com/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="slack",
        name="Slack",
        provider="Slack",
        description="Slack MCP server: channels, messages, search.",
        server_url="https://slack.com/api/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=False,
        oauth_default_scope="channels:read chat:write users:read",
        oauth_static_client_id_env=f"{_slug_to_env_prefix('slack')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('slack')}__CLIENT_SECRET",
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://api.slack.com/"},
    ),
    # All Cloudflare-hosted MCP servers use the streamable_http transport
    # at /mcp; /sse is the deprecated legacy path that DNS sometimes
    # serves and sometimes doesn't (e.g. workers.mcp.cloudflare.com
    # never had a public DNS record at all). Source:
    # https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/
    MCPConnectorTemplateSeedEntry(
        slug="cloudflare-api",
        name="Cloudflare API",
        provider="Cloudflare",
        description="Cloudflare's full API surface (2500+ endpoints) via Code Mode search/execute.",
        server_url="https://mcp.cloudflare.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://developers.cloudflare.com/api/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="cloudflare-workers",
        name="Cloudflare Workers",
        provider="Cloudflare",
        description="Build Workers with storage, AI and compute bindings (Workers Bindings server).",
        server_url="https://bindings.mcp.cloudflare.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://developers.cloudflare.com/workers/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="cloudflare-observability",
        name="Cloudflare Observability",
        provider="Cloudflare",
        description="Debug Worker / pipeline issues via logs, analytics and traces.",
        server_url="https://observability.mcp.cloudflare.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://developers.cloudflare.com/analytics/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="cloudflare-logs",
        name="Cloudflare Logs",
        provider="Cloudflare",
        description="Cloudflare Logpush job health summaries.",
        server_url="https://logs.mcp.cloudflare.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://developers.cloudflare.com/logs/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="cloudflare-radar",
        name="Cloudflare Radar",
        provider="Cloudflare",
        description="Internet traffic trends and URL scans via Cloudflare Radar.",
        server_url="https://radar.mcp.cloudflare.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://radar.cloudflare.com/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="sentry",
        name="Sentry",
        provider="Sentry",
        description="Sentry MCP server: issues, events, releases.",
        server_url="https://mcp.sentry.dev/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={"docs_url": "https://docs.sentry.io/"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="intercom",
        name="Intercom",
        provider="Intercom",
        description="Intercom MCP server: conversations, contacts, articles.",
        server_url="https://mcp.intercom.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=True,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={
            "docs_url": "https://developers.intercom.com/",
            "oauth_authorization_server_metadata_url": (
                "https://mcp.intercom.com/.well-known/oauth-authorization-server"
            ),
        },
    ),
    MCPConnectorTemplateSeedEntry(
        slug="gws",
        name="Google Workspace",
        provider="Google Workspace",
        description="Google Workspace MCP server: Gmail, Drive, Calendar, Docs.",
        server_url="https://www.googleapis.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        oauth_dcr_supported=False,
        oauth_default_scope=(
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/drive.readonly"
        ),
        oauth_static_client_id_env=f"{_slug_to_env_prefix('gws')}__CLIENT_ID",
        oauth_static_client_secret_env=f"{_slug_to_env_prefix('gws')}__CLIENT_SECRET",
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://developers.google.com/workspace"},
    ),
    MCPConnectorTemplateSeedEntry(
        slug="mslearn",
        name="Microsoft Learn",
        provider="Microsoft",
        description="Microsoft Learn MCP server: public docs search, no auth required.",
        server_url="https://learn.microsoft.com/api/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=None,
        static_auth_header_template=None,
        template_metadata={"docs_url": "https://learn.microsoft.com/"},
    ),
    # --- Search MCP sources (issue #148) ---
    # Hosted servers verified live with a real API key on 2026-05-27:
    #   - Tavily: ``https://mcp.tavily.com/mcp/`` accepts both
    #     ``Authorization: Bearer`` and ``?tavilyApiKey=...``; we use
    #     Bearer for parity with the rest of the catalog.
    #   - Exa:    ``https://mcp.exa.ai/mcp`` accepts ``x-api-key`` only.
    #   - Jina:   ``https://mcp.jina.ai/v1`` accepts ``Authorization: Bearer``.
    # Bocha and Perplexity intentionally NOT seeded — no official hosted
    # MCP endpoint at PR time. See spec Open Questions OQ-A / OQ-B.
    MCPConnectorTemplateSeedEntry(
        slug="tavily",
        name="Tavily",
        provider="Tavily",
        description="Tavily search MCP server: web search, news, and page extraction.",
        server_url="https://mcp.tavily.com/mcp/",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        static_auth_style="bearer",
        template_metadata={"docs_url": "https://docs.tavily.com/documentation/mcp"},
        tool_citation_defaults={
            "tavily_search": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "content"},
            },
            "tavily_extract": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "snippet": "raw_content"},
            },
        },
    ),
    MCPConnectorTemplateSeedEntry(
        slug="exa",
        name="Exa",
        provider="Exa",
        description="Exa search MCP server: web search, research papers, code search.",
        server_url="https://mcp.exa.ai/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=None,
        static_auth_style="header",
        static_auth_header_name="x-api-key",
        template_metadata={"docs_url": "https://exa.ai/docs/reference/exa-mcp"},
        tool_citation_defaults={
            "web_search_exa": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
            "research_paper_search_exa": {
                "content_type": "json",
                "source_type": "academic",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
            "code_search_exa": {
                "content_type": "json",
                "source_type": "code",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
            "web_fetch_exa": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {"url": "url", "title": "title", "snippet": "text"},
            },
        },
    ),
    MCPConnectorTemplateSeedEntry(
        slug="jina",
        name="Jina AI",
        provider="Jina AI",
        description="Jina AI MCP server: web search, URL reader, arXiv / SSRN search.",
        server_url="https://mcp.jina.ai/v1",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        static_auth_style="bearer",
        template_metadata={"docs_url": "https://jina.ai/api-dashboard/mcp"},
        tool_citation_defaults={
            # ``search_web`` mirrors the s.jina.ai REST shape: top-level
            # ``data`` array, each item with ``url``/``title``/``description``.
            "search_web": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "data",
                "mapping": {"url": "url", "title": "title", "snippet": "description"},
            },
            "search_arxiv": {
                "content_type": "json",
                "source_type": "academic",
                "content_field": "data",
                "mapping": {"url": "url", "title": "title", "snippet": "description"},
            },
            "search_ssrn": {
                "content_type": "json",
                "source_type": "academic",
                "content_field": "data",
                "mapping": {"url": "url", "title": "title", "snippet": "description"},
            },
            "read_url": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "data",
                "mapping": {"url": "url", "title": "title", "snippet": "content"},
            },
        },
    ),
    MCPConnectorTemplateSeedEntry(
        slug="webtools",
        name="WebTools",
        provider="Cubebox",
        description="Self-hosted WebTools MCP server: web_search and web_fetch.",
        server_url="http://localhost:8020/api/webtools",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="org",
        oauth_dcr_supported=None,
        oauth_default_scope=None,
        oauth_static_client_id_env=None,
        oauth_static_client_secret_env=None,
        static_form_schema=_TOKEN_FIELD,
        static_auth_header_template=_BEARER_TEMPLATE,
        template_metadata={},
        tool_citation_defaults={
            "web_search": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {
                    "url": "url",
                    "title": "title",
                    "snippet": "description",
                },
            },
            "web_fetch": {
                "content_type": "text",
                "source_type": "web",
                "content_field": None,
                "mapping": {"snippet": "text"},
                "args_mapping": {"url": "url"},
            },
        },
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


async def _sync_active_template_installs(
    session: AsyncSession,
    *,
    template_id: str,
    server_url: str,
    transport: str,
    static_auth_style: str,
    static_auth_header_name: str | None,
    static_auth_query_param: str | None,
) -> int:
    """Keep active installs created from a catalog template aligned with the template row."""
    stmt = select(MCPConnectorInstall).where(
        MCPConnectorInstall.template_id == template_id,  # type: ignore[arg-type]
        MCPConnectorInstall.install_state == "active",  # type: ignore[arg-type]
    )
    installs = list((await session.execute(stmt)).scalars().all())
    if not installs:
        return 0

    for install in installs:
        install.server_url = server_url
        install.server_url_hash = server_url_hash(server_url)
        install.transport = transport
        install.static_auth_style = static_auth_style
        install.static_auth_header_name = static_auth_header_name
        install.static_auth_query_param = static_auth_query_param
        install.updated_at = datetime.now(UTC)
        session.add(install)

    await session.flush()
    return len(installs)


async def seed_templates(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    get_env: Callable[[str], str | None] = os.getenv,
    catalog: list[MCPConnectorTemplateSeedEntry] | None = None,
) -> SeedResult:
    """Idempotent: upsert every connector template entry into the DB.

    For connectors that need a pre-registered OAuth client (DCR=False, e.g.
    GitHub / Slack / Google Workspace), reads the static client id / secret
    from environment variables, encrypts the secret into a system-level
    credential row, and links it on the template row. Missing env vars cause
    that single connector to be skipped with a warning — others continue.

    Returns a ``SeedResult`` summarizing the run. Run twice → no diffs.
    """
    repo = MCPConnectorTemplateRepository(session)
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
                msg = (
                    f"Skipping connector template '{entry.slug}': missing {id_env} or {secret_env}"
                )
                warnings.append(msg)
                skipped += 1
                active_slugs.append(entry.slug)
                continue
            client_id = raw_id
            client_secret_credential_id = await _upsert_oauth_client_secret(
                session, backend, slug=entry.slug, plaintext=raw_secret
            )

        template_row = await repo.upsert_by_slug(
            slug=entry.slug,
            name=entry.name,
            description=entry.description,
            provider=entry.provider,
            server_url=entry.server_url,
            transport=entry.transport,
            supported_auth_methods=list(entry.supported_auth_methods),
            default_credential_policy=entry.default_credential_policy,
            oauth_dcr_supported=entry.oauth_dcr_supported,
            oauth_default_scope=entry.oauth_default_scope,
            oauth_static_client_id=client_id,
            oauth_static_client_secret_credential_id=client_secret_credential_id,
            static_form_schema=entry.static_form_schema,
            static_auth_header_template=entry.static_auth_header_template,
            static_auth_style=entry.static_auth_style,
            static_auth_header_name=entry.static_auth_header_name,
            static_auth_query_param=entry.static_auth_query_param,
            template_metadata=dict(entry.template_metadata),
            tool_citation_defaults=dict(entry.tool_citation_defaults),
            status="active",
        )
        upserted += 1
        active_slugs.append(entry.slug)
        await _sync_active_template_installs(
            session,
            template_id=template_row.id,
            server_url=template_row.server_url,
            transport=template_row.transport,
            static_auth_style=template_row.static_auth_style,
            static_auth_header_name=template_row.static_auth_header_name,
            static_auth_query_param=template_row.static_auth_query_param,
        )

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
