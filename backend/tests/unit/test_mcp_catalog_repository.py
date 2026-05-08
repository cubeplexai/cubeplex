"""Tests for the system-level MCP catalog repository."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_upsert_by_slug_inserts_then_updates(session: AsyncSession) -> None:
    repo = MCPCatalogConnectorRepository(session)

    first = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="Old description",
        provider="GitHub",
        server_url="https://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="user",
    )
    assert first.id.startswith("mctlg-")
    assert first.description == "Old description"

    second = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="New description",
        provider="GitHub",
        server_url="https://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="user",
    )

    assert second.id == first.id
    assert second.description == "New description"
    assert len(await repo.list_active()) == 1


async def test_get_by_id_and_slug(session: AsyncSession) -> None:
    repo = MCPCatalogConnectorRepository(session)
    row = await repo.upsert_by_slug(
        slug="notion",
        name="Notion",
        description="d",
        provider="Notion",
        server_url="https://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_scope="user",
    )

    assert (await repo.get_by_id(row.id)).slug == "notion"  # type: ignore[union-attr]
    assert (await repo.get_by_slug("notion")).id == row.id  # type: ignore[union-attr]
    assert await repo.get_by_slug("missing") is None


async def test_mark_deprecated_for_missing_slugs(session: AsyncSession) -> None:
    repo = MCPCatalogConnectorRepository(session)

    keep_row = await repo.upsert_by_slug(
        slug="keep",
        name="Keep",
        description="d",
        provider="x",
        server_url="https://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_scope="none",
    )
    drop_row = await repo.upsert_by_slug(
        slug="drop",
        name="Drop",
        description="d",
        provider="x",
        server_url="https://example.com/mcp2",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_scope="none",
    )

    changed = await repo.mark_deprecated_for_missing_slugs(kept_slugs=["keep"])

    assert {row.id for row in changed} == {drop_row.id}
    refreshed_keep = await repo.get_by_id(keep_row.id)
    refreshed_drop = await repo.get_by_id(drop_row.id)
    assert refreshed_keep is not None and refreshed_keep.status == "active"
    assert refreshed_drop is not None and refreshed_drop.status == "deprecated"

    # Idempotent — second run is a no-op
    again = await repo.mark_deprecated_for_missing_slugs(kept_slugs=["keep"])
    assert again == []
