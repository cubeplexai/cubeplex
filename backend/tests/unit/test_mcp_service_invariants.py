"""Unit tests for MCPServerService invariant enforcement."""

from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.auth.context import RequestContext
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.exceptions import (
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
)
from cubebox.models import Role, User
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPBindingRepository,
    WorkspaceMCPCredentialRepository,
)
from cubebox.services.credential import CredentialService
from cubebox.services.mcp import MCPServerService


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
def mcp_service(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cred_service: CredentialService,
    request_context: RequestContext,
) -> MCPServerService:
    async def _discover_success(*_args: object, **_kwargs: object) -> tuple[bool, list, None]:
        return True, [], None

    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _discover_success)

    return MCPServerService(
        server_repo=MCPServerRepository(session, org_id=request_context.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=request_context.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=request_context.org_id),
        binding_repo=WorkspaceMCPBindingRepository(session, org_id=request_context.org_id),
        cred_service=cred_service,
        request_context=request_context,
    )


async def test_create_org_scope_requires_credential(mcp_service: MCPServerService) -> None:
    with pytest.raises(MCPCredentialRequired):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="static",
            credential_scope="org",
            credential_plaintext=None,
        )


async def test_create_user_scope_rejects_credential(mcp_service: MCPServerService) -> None:
    with pytest.raises(MCPUserScopeCredentialForbidden):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="static",
            credential_scope="user",
            credential_plaintext="should-not-be-here",
        )


async def test_create_oauth_v1_rejected(mcp_service: MCPServerService) -> None:
    with pytest.raises(MCPOAuthNotImplemented):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="org",
            credential_plaintext="x",
        )


async def test_duplicate_url_in_same_scope_conflicts(mcp_service: MCPServerService) -> None:
    await mcp_service.create(
        name="a",
        server_url="https://x",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerURLConflict):
        await mcp_service.create(
            name="b",
            server_url="https://x",
            transport="streamable_http",
            auth_method="none",
            credential_scope="none",
        )


async def test_duplicate_name_in_same_scope_conflicts(mcp_service: MCPServerService) -> None:
    await mcp_service.create(
        name="dup",
        server_url="https://a",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerNameConflict):
        await mcp_service.create(
            name="dup",
            server_url="https://b",
            transport="streamable_http",
            auth_method="none",
            credential_scope="none",
        )


async def test_update_renaming_to_existing_name_conflicts(
    mcp_service: MCPServerService,
) -> None:
    first = await mcp_service.create(
        name="a",
        server_url="https://x",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )
    await mcp_service.create(
        name="b",
        server_url="https://y",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerNameConflict):
        await mcp_service.update(server_id=first.id, name="b")


async def test_delete_cascades_and_removes_server(mcp_service: MCPServerService) -> None:
    server = await mcp_service.create(
        name="c",
        server_url="https://z",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    await mcp_service.delete(server_id=server.id)

    with pytest.raises(MCPServerNotFound):
        await mcp_service.update(server_id=server.id, name="x")


async def test_refresh_tools_updates_server_auth_state(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="refresh",
        server_url="https://refresh",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    refreshed = await mcp_service.refresh_tools(server_id=server.id)

    assert refreshed.authed is True
    assert refreshed.last_discovered_at is not None


async def test_test_connection_does_not_persist_server(
    mcp_service: MCPServerService,
) -> None:
    success, tools, error = await mcp_service.test_connection(
        server_url="https://dry-run",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    assert success is True
    assert tools == []
    assert error is None
    assert await mcp_service.server_repo.list_for_org() == []
