"""Install paths copy catalog.tool_citations into new MCPServer rows."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.auth.context import RequestContext
from cubebox.credentials.encryption import FernetBackend
from cubebox.models import MCPCatalogConnector, Role, User
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService
from cubebox.services.mcp_catalog import MCPCatalogService


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
def request_context() -> RequestContext:
    return RequestContext(
        user=User(id="u1", email="u1@example.com", hashed_password="x"),
        org_id="org-test",
        workspace_id="ws-test",
        role=Role.ADMIN,
    )


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


@pytest.fixture
def cred_service(
    session: AsyncSession,
    encryption_backend: FernetBackend,
    request_context: RequestContext,
) -> CredentialService:
    repo = CredentialRepository(session, org_id=request_context.org_id)
    return CredentialService(
        repo,
        encryption_backend,
        org_id=request_context.org_id,
        actor_user_id=request_context.user.id,
    )


@pytest.fixture
def service(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cred_service: CredentialService,
    request_context: RequestContext,
) -> MCPCatalogService:
    async def _discover_success(*_args: object, **_kwargs: object) -> tuple[bool, list, None]:
        return True, [], None

    monkeypatch.setattr(
        "cubebox.services.mcp_catalog.discover_tools",
        _discover_success,
    )

    return MCPCatalogService(
        catalog_repo=MCPCatalogConnectorRepository(session),
        server_repo=MCPServerRepository(session, org_id=request_context.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=request_context.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=request_context.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=request_context.org_id),
        cred_service=cred_service,
        request_context=request_context,
    )


async def _seed_catalog_with_citations(
    session: AsyncSession,
    *,
    slug: str,
    tool_citations: dict[str, dict[str, Any]],
) -> MCPCatalogConnector:
    repo = MCPCatalogConnectorRepository(session)
    return await repo.upsert_by_slug(
        slug=slug,
        name=slug.capitalize(),
        description="test connector",
        provider=slug.capitalize(),
        server_url=f"https://{slug}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static", "none"],
        default_credential_scope="org",
        static_auth_header_template="Bearer {token}",
        tool_citations=tool_citations,
    )


@pytest.mark.asyncio
async def test_install_for_org_copies_tool_citations(
    session: AsyncSession,
    service: MCPCatalogService,
) -> None:
    citations: dict[str, dict[str, Any]] = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": None,
            "mapping": {"snippet": "s"},
        }
    }
    connector = await _seed_catalog_with_citations(
        session, slug="t-org-citations", tool_citations=citations
    )

    result = await service.install_for_org(
        catalog_id=connector.id,
        auth_method="static",
        credential_plaintext="token-value",
        credential_name=None,
        auto_enable_workspaces=True,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.tool_citations == citations


@pytest.mark.asyncio
async def test_install_for_org_empty_citations_gives_empty_dict(
    session: AsyncSession,
    service: MCPCatalogService,
) -> None:
    connector = await _seed_catalog_with_citations(session, slug="t-org-empty", tool_citations={})

    result = await service.install_for_org(
        catalog_id=connector.id,
        auth_method="static",
        credential_plaintext="token-value",
        credential_name=None,
        auto_enable_workspaces=True,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.tool_citations == {}


@pytest.mark.asyncio
async def test_install_for_workspace_copies_tool_citations(
    session: AsyncSession,
    service: MCPCatalogService,
) -> None:
    citations: dict[str, dict[str, Any]] = {
        "web_fetch": {
            "content_type": "text",
            "source_type": "web",
            "content_field": None,
            "mapping": {"snippet": "text"},
        }
    }
    connector = await _seed_catalog_with_citations(
        session, slug="t-ws-citations", tool_citations=citations
    )

    result = await service.install_for_workspace(
        catalog_id=connector.id,
        workspace_id="ws-test",
        auth_method="static",
        credential_plaintext="token-value",
        credential_name=None,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.tool_citations == citations


@pytest.mark.asyncio
async def test_install_for_workspace_empty_citations_gives_empty_dict(
    session: AsyncSession,
    service: MCPCatalogService,
) -> None:
    connector = await _seed_catalog_with_citations(session, slug="t-ws-empty", tool_citations={})

    result = await service.install_for_workspace(
        catalog_id=connector.id,
        workspace_id="ws-test",
        auth_method="static",
        credential_plaintext="token-value",
        credential_name=None,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.tool_citations == {}


@pytest.mark.asyncio
async def test_install_for_org_deep_copies_tool_citations(
    session: AsyncSession,
    service: MCPCatalogService,
) -> None:
    """Mutating the server's inner dict must not bleed back into the catalog."""
    inner: dict[str, Any] = {"snippet": "description"}
    catalog = await _seed_catalog_with_citations(
        session,
        slug="t-deep",
        tool_citations={
            "foo": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": inner,
            }
        },
    )
    result = await service.install_for_org(
        catalog_id=catalog.id,
        auth_method="static",
        credential_plaintext="x",
        credential_name=None,
        auto_enable_workspaces=True,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    # Mutate the inner dict on the server; the catalog row must remain unchanged.
    server.tool_citations["foo"]["mapping"]["snippet"] = "MUTATED"
    assert catalog.tool_citations["foo"]["mapping"]["snippet"] == "description"
