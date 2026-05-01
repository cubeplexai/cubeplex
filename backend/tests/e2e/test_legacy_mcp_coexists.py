"""E2E test that legacy config MCP tools coexist with DB-scoped MCP tools."""

from typing import Any, cast

import httpx
import pytest
from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.config import config
from cubebox.db.engine import _build_database_url
from cubebox.mcp.runtime import load_mcp_tools_for_workspace
from cubebox.mcp.user_token import HS256Signer
from cubebox.models import MCPServer, Workspace
from cubebox.services.credential import CredentialService
from cubebox.tools import get_registry, init_mcp_tools
from cubebox.tools.registry import ToolRegistry


def _legacy_echo(value: str) -> str:
    return value


def _db_echo(value: str) -> str:
    return value


async def test_legacy_config_tools_and_db_tools_both_load(
    admin_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, workspace_id = admin_client
    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]

    import cubebox.mcp.client
    import cubebox.tools

    isolated_registry = ToolRegistry()
    legacy_tool = StructuredTool.from_function(
        _legacy_echo,
        name="legacy_echo",
        description="Legacy MCP echo",
    )
    db_tool = StructuredTool.from_function(
        _db_echo,
        name="db_echo",
        description="DB MCP echo",
    )

    original_get = config.get

    def _config_get(key: str, default: Any = None, **kwargs: Any) -> Any:
        if key == "mcp.enabled":
            return True
        return original_get(key, default, **kwargs)

    async def _load_legacy_config_servers() -> list[BaseTool]:
        return [legacy_tool]

    monkeypatch.setattr(config, "get", _config_get)
    monkeypatch.setattr(cubebox.tools, "_registry", isolated_registry)
    monkeypatch.setattr(
        cubebox.mcp.client.MCPManager,
        "load_legacy_config_servers",
        _load_legacy_config_servers,
    )

    await init_mcp_tools()
    assert "legacy_echo" in get_registry().list_tool_names()

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "db-coexistence",
            "server_url": "http://127.0.0.1:9/db-coexistence",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    captured_connection_params: dict[str, Any] = {}

    def _construct(
        cache: list[dict[str, Any]],
        connection_params: dict[str, Any],
    ) -> list[BaseTool]:
        assert cache[0]["name"] == "db_echo"
        captured_connection_params.update(connection_params)
        return [db_tool]

    monkeypatch.setattr("cubebox.mcp.runtime.construct_basetools_from_cache", _construct)

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            workspace = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_id))
            ).scalar_one()
            server = (
                await session.execute(select(MCPServer).where(MCPServer.id == server_id))
            ).scalar_one()
            server.authed = True
            server.tools_cache = [
                {
                    "name": "db_echo",
                    "description": "DB MCP echo",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
            session.add(server)
            await session.commit()

            db_tools = await load_mcp_tools_for_workspace(
                org_id=workspace.org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                cred_service=cast(CredentialService, object()),
                signer=HS256Signer(config.get("auth.jwt_secret")),
                session=session,
            )
    finally:
        await engine.dispose()

    legacy_names = get_registry().list_tool_names()
    db_names = [tool.name for tool in db_tools]
    assert "legacy_echo" in legacy_names
    assert "db_echo" in db_names
    assert captured_connection_params["headers"]["Authorization"].startswith("Bearer ")
