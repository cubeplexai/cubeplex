"""Unit tests for per-run DB MCP tool assembly."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, cast

import pytest
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models import MCPServer
from cubebox.repositories.mcp import MCPServerRepository
from cubebox.services.credential import CredentialService


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


def _server(**overrides: object) -> MCPServer:
    values: dict[str, object] = {
        "org_id": "org-1",
        "owner_workspace_id": "ws-1",
        "name": "db-mcp",
        "server_url": "https://mcp.example.com",
        "server_url_hash": "hash",
        "transport": "streamable_http",
        "auth_method": "none",
        "credential_scope": "none",
        "credential_id": None,
        "headers": {},
        "tools_cache": [{"name": "echo", "description": "Echo", "input_schema": {}}],
        "authed": True,
        "created_by_user_id": "u-creator",
    }
    values.update(overrides)
    return MCPServer(**values)


class _Signer:
    async def sign(
        self,
        *,
        user_id: str,
        org_id: str,
        workspace_id: str,
        mcp_server_id: str,
        ttl: timedelta,
    ) -> str:
        return f"{user_id}:{org_id}:{workspace_id}:{mcp_server_id}:{int(ttl.total_seconds())}"


async def test_load_db_servers_builds_tools_from_visible_server_cache(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    from cubebox.mcp.runtime import load_db_servers_for_workspace

    server = await MCPServerRepository(session, org_id="org-1").add(_server())
    captured: dict[str, Any] = {}
    fake_tool = cast(BaseTool, object())

    def _construct(
        cache: list[dict[str, Any]],
        connection_params: dict[str, Any],
    ) -> list[BaseTool]:
        captured["cache"] = cache
        captured["connection_params"] = connection_params
        return [fake_tool]

    monkeypatch.setattr("cubebox.mcp.runtime.construct_basetools_from_cache", _construct)

    tools = await load_db_servers_for_workspace(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="u-1",
        cred_service=cast(CredentialService, object()),
        signer=_Signer(),
        session=session,
    )

    assert tools == [fake_tool]
    assert captured["cache"] == server.tools_cache
    assert captured["connection_params"]["headers"]["Authorization"].startswith(
        "Bearer u-1:org-1:ws-1:"
    )


async def test_load_db_servers_skips_user_scope_without_user_credential(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    from cubebox.mcp.runtime import load_db_servers_for_workspace

    await MCPServerRepository(session, org_id="org-1").add(
        _server(auth_method="static", credential_scope="user")
    )
    constructed = False

    def _construct(
        _cache: list[dict[str, Any]],
        _connection_params: dict[str, Any],
    ) -> list[BaseTool]:
        nonlocal constructed
        constructed = True
        return []

    monkeypatch.setattr("cubebox.mcp.runtime.construct_basetools_from_cache", _construct)

    tools = await load_db_servers_for_workspace(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="u-1",
        cred_service=cast(CredentialService, object()),
        signer=_Signer(),
        session=session,
    )

    assert tools == []
    assert constructed is False
