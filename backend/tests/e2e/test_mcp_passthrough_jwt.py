"""E2E coverage for no-auth MCP passthrough JWT assembly."""

from typing import Any, cast

import httpx
import jwt
import pytest
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.config import config
from cubebox.db.engine import _build_database_url
from cubebox.mcp.runtime import load_db_servers_for_workspace
from cubebox.models import MCPServer, Workspace
from cubebox.services.credential import CredentialService


async def test_no_auth_mcp_server_gets_run_scoped_passthrough_jwt(
    admin_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, workspace_id = admin_client
    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]

    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": "passthrough-jwt",
            "server_url": "http://127.0.0.1:9/passthrough-jwt",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    server_id = create_resp.json()["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
                    "name": "whoami",
                    "description": "Return caller identity",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
            session.add(server)
            await session.commit()

            tools = await load_db_servers_for_workspace(
                org_id=workspace.org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                cred_service=cast(CredentialService, object()),
                signer=app_signer(),
                session=session,
            )
    finally:
        await engine.dispose()

    assert tools == [fake_tool]
    assert captured["cache"][0]["name"] == "whoami"
    auth_header = captured["connection_params"]["headers"]["Authorization"]
    assert auth_header.startswith("Bearer ")

    token = auth_header.removeprefix("Bearer ")
    claims = jwt.decode(
        token,
        config.get("auth.jwt_secret"),
        algorithms=["HS256"],
        issuer="cubebox",
    )
    assert claims["sub"] == user_id
    assert claims["org"]
    assert claims["ws"] == workspace_id
    assert claims["mcp"] == server_id


def app_signer() -> object:
    from cubebox.mcp.user_token import HS256Signer

    return HS256Signer(config.get("auth.jwt_secret"))
