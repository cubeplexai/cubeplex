"""Tests for the v1 MCP connector template seeder."""

import dataclasses
from collections.abc import AsyncIterator, Callable

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.template_seed import (
    CATALOG,
    MCPConnectorTemplateSeedEntry,
    seed_templates,
)
from cubebox.models import Credential, MCPConnectorTemplate
from cubebox.repositories.mcp import MCPConnectorTemplateRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


def _full_env() -> dict[str, str]:
    """Env-var dict that satisfies every static-OAuth-app connector in CATALOG."""
    env: dict[str, str] = {}
    for entry in CATALOG:
        if entry.oauth_static_client_id_env is not None:
            env[entry.oauth_static_client_id_env] = f"client-id-for-{entry.slug}"
        if entry.oauth_static_client_secret_env is not None:
            env[entry.oauth_static_client_secret_env] = f"client-secret-for-{entry.slug}"
    return env


def _make_get_env(values: dict[str, str]) -> Callable[[str], str | None]:
    def _getter(key: str) -> str | None:
        return values.get(key)

    return _getter


def test_intercom_catalog_declares_authorization_server_metadata_url() -> None:
    intercom = next(entry for entry in CATALOG if entry.slug == "intercom")

    assert intercom.template_metadata["oauth_authorization_server_metadata_url"] == (
        "https://mcp.intercom.com/.well-known/oauth-authorization-server"
    )


async def test_seed_with_full_env_writes_templates_and_credentials(
    session: AsyncSession, backend: FernetBackend
) -> None:
    env = _full_env()
    result = await seed_templates(session, backend, get_env=_make_get_env(env))

    assert result.skipped == 0
    assert result.upserted == len(CATALOG)
    assert result.deprecated == 0
    assert result.warnings == []

    repo = MCPConnectorTemplateRepository(session)
    rows = await repo.list_active()
    assert {row.slug for row in rows} == {entry.slug for entry in CATALOG}

    # Static OAuth client secret credentials are written for every
    # connector that exposed an env var pair.
    expected_secret_slugs = {
        entry.slug for entry in CATALOG if entry.oauth_static_client_secret_env is not None
    }
    cred_rows = (
        (
            await session.execute(
                select(Credential).where(
                    Credential.org_id.is_(None),  # type: ignore[union-attr]
                    Credential.kind == "mcp_oauth_client_secret",  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.cred_metadata.get("slug") for row in cred_rows} == expected_secret_slugs

    # Template rows for those slugs link the credential id.
    rows_by_slug = {row.slug: row for row in rows}
    for slug in expected_secret_slugs:
        row = rows_by_slug[slug]
        assert row.oauth_static_client_id == f"client-id-for-{slug}"
        assert row.oauth_static_client_secret_credential_id is not None

    # Stored ciphertext round-trips through the configured backend.
    sample = next(row for row in cred_rows)
    plaintext = (await backend.decrypt(sample.value_encrypted)).decode("utf-8")
    assert plaintext.startswith("client-secret-for-")


async def test_seed_skips_static_oauth_connector_when_env_missing(
    session: AsyncSession, backend: FernetBackend
) -> None:
    # Drop only github's secret env so other DCR-less connectors still seed.
    env = _full_env()
    env.pop("CUBEBOX_MCP_OAUTH__GITHUB__CLIENT_SECRET")

    result = await seed_templates(session, backend, get_env=_make_get_env(env))

    assert result.skipped == 1
    assert any("github" in w.lower() for w in result.warnings)
    assert result.upserted == len(CATALOG) - 1

    repo = MCPConnectorTemplateRepository(session)
    slugs = {row.slug for row in await repo.list_active()}
    assert "github" not in slugs
    # Other connectors still made it through.
    assert "notion" in slugs
    assert "mslearn" in slugs


async def test_seed_marks_removed_slugs_as_deprecated(
    session: AsyncSession, backend: FernetBackend
) -> None:
    env = _full_env()
    await seed_templates(session, backend, get_env=_make_get_env(env))

    # Re-run with one connector dropped. Its row should flip to
    # deprecated rather than being deleted (preserves install FK refs).
    pruned = [entry for entry in CATALOG if entry.slug != "mslearn"]
    second = await seed_templates(session, backend, get_env=_make_get_env(env), catalog=pruned)

    assert second.deprecated == 1
    repo = MCPConnectorTemplateRepository(session)
    deprecated_row = await repo.get_by_slug("mslearn")
    assert deprecated_row is not None
    assert deprecated_row.status == "deprecated"


async def test_seed_is_idempotent(session: AsyncSession, backend: FernetBackend) -> None:
    env = _full_env()
    first = await seed_templates(session, backend, get_env=_make_get_env(env))
    second = await seed_templates(session, backend, get_env=_make_get_env(env))

    assert first.upserted == second.upserted
    assert second.deprecated == 0

    rows1 = (await session.execute(select(MCPConnectorTemplate))).scalars().all()
    assert len(rows1) == len(CATALOG)


async def test_seed_persists_tool_citation_defaults(
    session: AsyncSession, backend: FernetBackend
) -> None:
    """tool_citation_defaults on a seed entry round-trips through upsert; update overwrites."""
    initial_citations = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "results",
            "mapping": {"url": "url", "snippet": "description"},
        },
    }
    catalog = [
        MCPConnectorTemplateSeedEntry(
            slug="webtools-test",
            name="WebTools Test",
            provider="Cubebox",
            description="test entry",
            server_url="http://example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["static"],
            default_credential_policy="org",
            oauth_dcr_supported=None,
            oauth_default_scope=None,
            oauth_static_client_id_env=None,
            oauth_static_client_secret_env=None,
            static_form_schema=None,
            static_auth_header_template=None,
            template_metadata={},
            tool_citation_defaults=initial_citations,
        )
    ]
    result = await seed_templates(session, backend, get_env=lambda _k: None, catalog=catalog)
    assert result.skipped == 0
    repo = MCPConnectorTemplateRepository(session)
    row = await repo.get_by_slug("webtools-test")
    assert row is not None
    assert row.tool_citation_defaults == initial_citations

    # Update path: re-seed with a different mapping → DB row updated.
    updated_citations = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "data",
            "mapping": {"url": "url", "snippet": "summary"},
        },
    }
    updated_catalog = [dataclasses.replace(catalog[0], tool_citation_defaults=updated_citations)]
    await seed_templates(session, backend, get_env=lambda _k: None, catalog=updated_catalog)
    row = await repo.get_by_slug("webtools-test")
    assert row is not None
    assert row.tool_citation_defaults == updated_citations


async def test_seed_with_custom_catalog(session: AsyncSession, backend: FernetBackend) -> None:
    """The seeder accepts an injected catalog list — handy for tests."""
    custom = [
        MCPConnectorTemplateSeedEntry(
            slug="testonly",
            name="Test Only",
            provider="Test",
            description="d",
            server_url="https://example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["none"],
            default_credential_policy="none",
            oauth_dcr_supported=None,
            oauth_default_scope=None,
            oauth_static_client_id_env=None,
            oauth_static_client_secret_env=None,
            static_form_schema=None,
            static_auth_header_template=None,
        )
    ]
    result = await seed_templates(session, backend, get_env=lambda _k: None, catalog=custom)
    assert result.upserted == 1
    repo = MCPConnectorTemplateRepository(session)
    row = await repo.get_by_slug("testonly")
    assert row is not None
    assert row.supported_auth_methods == ["none"]


def test_webtools_entry_has_web_search_and_web_fetch_citations() -> None:
    """The webtools seed entry must carry citation mappings for both tools."""
    by_slug = {e.slug: e for e in CATALOG}
    assert "webtools" in by_slug
    entry = by_slug["webtools"]

    assert "web_search" in entry.tool_citation_defaults
    web_search = entry.tool_citation_defaults["web_search"]
    assert web_search["content_type"] == "json"
    assert web_search["source_type"] == "web"
    assert web_search["content_field"] == "results"
    assert web_search["mapping"]["url"] == "url"
    assert web_search["mapping"]["title"] == "title"
    assert web_search["mapping"]["snippet"] == "description"

    assert "web_fetch" in entry.tool_citation_defaults
    web_fetch = entry.tool_citation_defaults["web_fetch"]
    assert web_fetch["content_type"] == "text"
    assert web_fetch["source_type"] == "web"
    assert web_fetch["content_field"] is None
    assert web_fetch["mapping"]["snippet"] == "text"
    assert web_fetch["args_mapping"]["url"] == "url"


def test_all_seed_tool_citations_are_valid_citation_configs() -> None:
    """Every tool_citation_defaults entry across CATALOG must be a valid CitationConfig."""
    from cubebox.middleware.citations.config import CitationConfig

    for entry in CATALOG:
        for tool_name, raw in entry.tool_citation_defaults.items():
            try:
                CitationConfig(**raw)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"{entry.slug}.{tool_name}: invalid CitationConfig — {exc}")
