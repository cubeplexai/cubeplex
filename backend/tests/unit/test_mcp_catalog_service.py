"""Unit tests for MCPCatalogService install paths and listing."""

from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.auth.context import RequestContext
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.exceptions import (
    MCPCatalogAuthMethodUnsupported,
    MCPCatalogInstallExists,
    MCPCredentialRequired,
)
from cubebox.models import MCPCatalogConnector, MCPServer, Role, User
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


async def _make_connector(
    session: AsyncSession,
    *,
    slug: str = "github",
    supported: list[str] | None = None,
    static_template: str | None = "Bearer {token}",
) -> MCPCatalogConnector:
    repo = MCPCatalogConnectorRepository(session)
    return await repo.upsert_by_slug(
        slug=slug,
        name=slug.capitalize(),
        description="x",
        provider=slug.capitalize(),
        server_url=f"https://{slug}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=supported or ["oauth", "static", "none"],
        default_credential_scope="org",
        static_auth_header_template=static_template,
    )


async def test_install_for_org_static_writes_credential_and_authed_true(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    result = await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="static",
        credential_plaintext="ghp_secret",
        credential_name=None,
        auto_enable_workspaces=True,
    )
    assert result.requires_oauth is False

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.catalog_connector_id == connector.id
    assert server.owner_workspace_id is None
    assert server.credential_scope == "org"
    assert server.credential_id is not None
    assert server.authed is True
    assert server.last_discovered_at is not None


async def test_install_for_org_oauth_returns_requires_oauth(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    result = await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="oauth",
        credential_plaintext=None,
        credential_name=None,
        auto_enable_workspaces=True,
    )

    assert result.requires_oauth is True
    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.auth_method == "oauth"
    assert server.credential_id is None
    assert server.authed is False


async def test_install_for_org_none_authed_immediately(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(
        session,
        slug="mslearn",
        supported=["none"],
        static_template=None,
    )

    result = await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="none",
        credential_plaintext=None,
        credential_name=None,
        auto_enable_workspaces=True,
    )

    assert result.requires_oauth is False
    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.credential_scope == "none"
    assert server.credential_id is None
    assert server.authed is True


async def test_install_for_workspace_forces_user_scope(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    result = await service.install_for_workspace(
        catalog_id=connector.id,
        workspace_id="ws-test",
        auth_method="static",
        credential_plaintext="user-token",
        credential_name=None,
    )

    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.owner_workspace_id == "ws-test"
    assert server.credential_scope == "user"
    # The user_mcp_credentials row carries the credential id, NOT the
    # mcp_servers row directly.
    assert server.credential_id is None
    user_cred = await service.user_cred_repo.get(user_id="u1", mcp_server_id=server.id)
    assert user_cred is not None
    assert server.authed is True


async def test_install_for_org_unsupported_auth_method_raises(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session, supported=["oauth"])

    with pytest.raises(MCPCatalogAuthMethodUnsupported):
        await service.install_for_org(
            catalog_id=connector.id,
            scope="org",
            auth_method="static",
            credential_plaintext="x",
            credential_name=None,
            auto_enable_workspaces=True,
        )


async def test_install_for_org_static_requires_credential(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    with pytest.raises(MCPCredentialRequired):
        await service.install_for_org(
            catalog_id=connector.id,
            scope="org",
            auth_method="static",
            credential_plaintext=None,
            credential_name=None,
            auto_enable_workspaces=True,
        )


async def test_duplicate_install_raises(session: AsyncSession, service: MCPCatalogService) -> None:
    connector = await _make_connector(session)

    await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="none",
        credential_plaintext=None,
        credential_name=None,
        auto_enable_workspaces=True,
    )
    # Need to mark connector as supporting "none" — refresh.
    # The connector fixture above uses {oauth, static, none}.

    with pytest.raises(MCPCatalogInstallExists):
        await service.install_for_org(
            catalog_id=connector.id,
            scope="org",
            auth_method="none",
            credential_plaintext=None,
            credential_name=None,
            auto_enable_workspaces=True,
        )


async def test_list_for_member_reports_org_install_status(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    # Before install — no statuses set.
    items = await service.list_for_member("ws-test")
    assert len(items) == 1
    assert items[0].org_install_id is None
    assert items[0].user_install_id is None
    assert items[0].workspace_visible is False

    await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="static",
        credential_plaintext="t",
        credential_name=None,
        auto_enable_workspaces=True,
    )

    items = await service.list_for_member("ws-test")
    assert items[0].org_install_id is not None
    assert items[0].workspace_visible is True
    assert items[0].user_install_id is None


async def test_list_for_member_reports_user_install_status(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)

    await service.install_for_workspace(
        catalog_id=connector.id,
        workspace_id="ws-test",
        auth_method="static",
        credential_plaintext="t",
        credential_name=None,
    )

    items = await service.list_for_member("ws-test")
    assert items[0].user_install_id is not None
    assert items[0].org_install_id is None
    # Workspace-scoped user install also makes the connector visible.
    assert items[0].workspace_visible is True


async def test_list_for_member_filters_by_query_and_provider(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    await _make_connector(session, slug="github")
    await _make_connector(session, slug="notion")

    by_q = await service.list_for_member("ws-test", q="github")
    assert {item.connector.slug for item in by_q} == {"github"}

    by_provider = await service.list_for_member("ws-test", provider="Notion")
    assert {item.connector.slug for item in by_provider} == {"notion"}


async def test_switch_auth_method_to_static_workspace_scope_creates_credential(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    """``credential_scope=workspace`` is reachable via static install paths in
    ``services/mcp.py``; the catalog seeder doesn't currently emit workspace-
    scope connectors but ``switch_auth_method`` must still handle pre-existing
    rows. Construct one directly and assert the workspace branch writes a
    ``WorkspaceMCPCredential`` row.
    """
    connector = await _make_connector(session)

    # Construct a workspace-scope server row directly. Static MCP servers
    # configured via ``services/mcp.py`` can have credential_scope=workspace,
    # and the user may later flip auth methods on them.
    server_repo = service.server_repo
    server = await server_repo.add(
        MCPServer(
            org_id="org-test",
            owner_workspace_id="ws-test",
            catalog_connector_id=connector.id,
            name="catalog:github:ws:ws-test",
            server_url=connector.server_url,
            server_url_hash="hash-x",
            transport=connector.transport,
            auth_method="oauth",
            credential_scope="workspace",
            credential_id=None,
            headers={},
            timeout=30.0,
            sse_read_timeout=300.0,
            created_by_user_id="u1",
        )
    )

    result = await service.switch_auth_method(
        install_id=server.id,
        new_auth_method="static",
        credential_plaintext="ws-token",
    )

    assert result.requires_oauth is False
    refreshed = await service.server_repo.get(server.id)
    assert refreshed is not None
    assert refreshed.auth_method == "static"
    assert refreshed.credential_scope == "workspace"
    # The credential lives on the workspace_mcp_credentials row, not on
    # the mcp_servers row directly.
    assert refreshed.credential_id is None
    ws_cred = await service.ws_cred_repo.get(workspace_id="ws-test", mcp_server_id=server.id)
    assert ws_cred is not None
    assert ws_cred.credential_id is not None


async def test_delete_install_clears_credentials_and_unauths(
    session: AsyncSession, service: MCPCatalogService
) -> None:
    connector = await _make_connector(session)
    result = await service.install_for_org(
        catalog_id=connector.id,
        scope="org",
        auth_method="static",
        credential_plaintext="t",
        credential_name=None,
        auto_enable_workspaces=True,
    )
    server = await service.server_repo.get(result.install_id)
    assert server is not None
    assert server.credential_id is not None

    await service.delete_install(result.install_id)

    refreshed = await service.server_repo.get(result.install_id)
    assert refreshed is not None
    assert refreshed.credential_id is None
    assert refreshed.authed is False
