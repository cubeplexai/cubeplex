"""E2E coverage for the lost-UI restoration features."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _resolve_org_user_for_client(
    client: httpx.AsyncClient,
    workspace_id: str,
) -> tuple[str, str]:
    """Return ``(org_id, user_id)`` for an authenticated admin client."""
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    workspaces = ws_resp.json()
    org_id = next(w["org_id"] for w in workspaces if w["id"] == workspace_id)
    return org_id, user_id


@pytest_asyncio.fixture
async def seeded_static_org_install_with_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Org-scope static install pre-populated with two fake tools and a
    citation mapping for one of them."""
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)
    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnectorInstall

        install = MCPConnectorInstall(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="seeded-with-tools",
            server_url="https://seeded.example.com/mcp",
            server_url_hash=server_url_hash("https://seeded.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="org",
            auth_status="pending",
            install_state="active",
            tools_cache=[
                {
                    "name": "ping",
                    "description": "say hi",
                    "input_schema": {"type": "object"},
                },
                {
                    "name": "pong",
                    "description": "say bye",
                    "input_schema": {"type": "object"},
                },
            ],
            tool_citations={
                "ping": {
                    "content_type": "json",
                    "source_type": "api",
                    "content_field": None,
                    "mapping": {"snippet": ""},
                }
            },
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        return install.id


# ---------------------------------------------------------------------------
# Task 1 — DTO exposes tools + tool_citations.
# ---------------------------------------------------------------------------


async def test_install_dto_exposes_tools_and_tool_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_with_tools_cache: str,
) -> None:
    """``MCPConnectorInstallOut`` must expose the tools list (not just
    tool_count) and tool_citations dict (for org admin callers)."""
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.get(f"/api/v1/admin/mcp/installs/{install_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "tools" in body, "tools field missing from install DTO"
    assert isinstance(body["tools"], list)
    if body["tools"]:
        sample = body["tools"][0]
        assert {"name", "description", "input_schema"} <= sample.keys()
    assert "tool_citations" in body
    assert isinstance(body["tool_citations"], dict) or body["tool_citations"] is None


# ---------------------------------------------------------------------------
# Task 2 — Discovery service writes tools_cache.
# ---------------------------------------------------------------------------


async def test_discover_tools_for_install_writes_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery service should fetch tools via cubepi and persist
    the result into install.tools_cache / .discovery_status."""
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)

    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnectorInstall

        install = MCPConnectorInstall(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="disc-test",
            server_url="https://disc.example.com/mcp",
            server_url_hash=server_url_hash("https://disc.example.com/mcp"),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        install_id = install.id

    # Stub the cubepi helper used inside discover_tools_for_install.
    async def fake_load(*args: object, **kwargs: object) -> list[object]:
        return [
            SimpleNamespace(
                name="ping",
                description="say hi",
                input_schema={"type": "object"},
            ),
            SimpleNamespace(
                name="pong",
                description="say bye",
                input_schema={"type": "object"},
            ),
        ]

    monkeypatch.setattr("cubebox.services.mcp_discovery.load_mcp_tools_http", fake_load)

    from cubebox.credentials.dependencies import build_credential_service
    from cubebox.credentials.encryption import FernetBackend
    from cubebox.mcp.dependencies import build_user_token_signer
    from cubebox.services.mcp_discovery import discover_tools_for_install

    backend = FernetBackend([_test_fernet_key().encode()])
    async with db_session_maker() as session:
        cred_service = build_credential_service(
            session, backend, org_id=org_id, actor_user_id=user_id
        )
        signer = build_user_token_signer()
        result = await discover_tools_for_install(
            install_id=install_id,
            workspace_id=None,
            actor_user_id=user_id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=None,  # type: ignore[arg-type]
        )
        assert result.discovery_status == "ok"
        assert result.tool_count == 2
        names = sorted(t["name"] for t in result.tools_cache_raw)
        assert names == ["ping", "pong"]
        assert result.last_error is None


def _test_fernet_key() -> str:
    """Return a deterministic Fernet key for tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()
