"""End-to-end: GET/PATCH /ws/{wsId}/mcp/servers/{id}/tool-citations."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.models import MCPServer, OrgRole, Role

# The directory-based e2e marker is applied automatically by the conftest hook;
# no explicit pytestmark is needed.

# ---------------------------------------------------------------------------
# Shared payload factories
# ---------------------------------------------------------------------------

_BASE_SERVER_PAYLOAD: dict[str, Any] = {
    "name": "citations-test-mcp",
    "server_url": "http://127.0.0.1:9/citations-test",
    "transport": "streamable_http",
    "auth_method": "none",
    "credential_scope": "none",
    "timeout": 1.0,
    "sse_read_timeout": 1.0,
}

# A valid CitationConfig dict
_VALID_CITATION: dict[str, Any] = {
    "content_type": "json",
    "source_type": "web",
    "content_field": "results",
    "mapping": {"url": "link", "snippet": "text"},
}

# Tools loaded into tools_cache for the default seeded server
_TOOLS_CACHE: list[dict[str, Any]] = [
    {"name": "web_search", "description": "Search the web", "input_schema": {}},
    {"name": "web_fetch", "description": "Fetch a URL", "input_schema": {}},
]

_INITIAL_CITATIONS: dict[str, Any] = {
    "web_search": _VALID_CITATION,
}


async def _create_server(
    client: httpx.AsyncClient,
    workspace_id: str,
    *,
    name: str = "citations-test-mcp",
) -> str:
    """POST a workspace-owned MCP server; return its id."""
    payload = dict(_BASE_SERVER_PAYLOAD)
    payload["name"] = name
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_tools_and_citations(
    db_session: AsyncSession,
    server_id: str,
    *,
    tools_cache: list[dict[str, Any]],
    tool_citations: dict[str, Any],
) -> None:
    """Directly patch tools_cache and tool_citations on the MCPServer row."""
    server = await db_session.get(MCPServer, server_id)
    assert server is not None, f"MCPServer {server_id} not found"
    server.tools_cache = tools_cache
    server.tool_citations = tool_citations
    await db_session.commit()
    await db_session.refresh(server)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def server_with_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> tuple[httpx.AsyncClient, str, str]:
    """Admin client + workspace + a server seeded with known tools_cache and tool_citations.

    Yields (client, workspace_id, server_id).
    """
    client, workspace_id = admin_client
    server_id = await _create_server(client, workspace_id, name="citations-happy-path")
    await _seed_tools_and_citations(
        db_session,
        server_id,
        tools_cache=_TOOLS_CACHE,
        tool_citations=_INITIAL_CITATIONS,
    )
    return client, workspace_id, server_id


@pytest_asyncio.fixture
async def server_with_orphan_citation(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> tuple[httpx.AsyncClient, str, str]:
    """Server whose tool_citations includes a key not in tools_cache.

    Yields (client, workspace_id, server_id).
    """
    client, workspace_id = admin_client
    server_id = await _create_server(client, workspace_id, name="citations-orphan")
    await _seed_tools_and_citations(
        db_session,
        server_id,
        tools_cache=_TOOLS_CACHE,
        tool_citations={
            "web_search": _VALID_CITATION,
            "old_tool": _VALID_CITATION,  # this key is not in _TOOLS_CACHE
        },
    )
    return client, workspace_id, server_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_tool_citations_returns_full_shape(
    server_with_citations: tuple[httpx.AsyncClient, str, str],
) -> None:
    """GET returns all expected top-level keys and correct values for a healthy server."""
    client, workspace_id, server_id = server_with_citations

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["server_id"] == server_id
    assert isinstance(body["tools_cache"], list)
    assert len(body["tools_cache"]) == len(_TOOLS_CACHE)
    assert "web_search" in body["tool_citations"]
    assert body["orphan_keys"] == []
    # This server has no catalog_connector_id, so catalog_defaults must be null.
    assert body["catalog_defaults"] is None


async def test_get_tool_citations_surfaces_orphans(
    server_with_orphan_citation: tuple[httpx.AsyncClient, str, str],
) -> None:
    """GET exposes orphan_keys when tool_citations contains a key absent from tools_cache."""
    client, workspace_id, server_id = server_with_orphan_citation

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "old_tool" in body["orphan_keys"]
    # The valid key must still be present in tool_citations.
    assert "web_search" in body["tool_citations"]


async def test_patch_tool_citations_replaces_state(
    server_with_citations: tuple[httpx.AsyncClient, str, str],
) -> None:
    """PATCH replaces tool_citations in full and the new state is reflected by GET."""
    client, workspace_id, server_id = server_with_citations

    new_citations: dict[str, Any] = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "data",
            "mapping": {"url": "u", "snippet": "s"},
        }
    }
    patch_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": new_citations},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["tool_citations"] == new_citations
    assert patch_resp.json()["orphan_keys"] == []

    # Persist check via GET
    get_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["tool_citations"] == new_citations


async def test_patch_rejects_unknown_tool_name(
    server_with_citations: tuple[httpx.AsyncClient, str, str],
) -> None:
    """PATCH with a tool name not in tools_cache returns 422."""
    client, workspace_id, server_id = server_with_citations

    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations",
        json={
            "tool_citations": {
                "ghost_tool": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": None,
                    "mapping": {},
                }
            }
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert any(d.get("tool") == "ghost_tool" for d in detail)


async def test_patch_rejects_invalid_citation_config(
    server_with_citations: tuple[httpx.AsyncClient, str, str],
) -> None:
    """PATCH with an invalid CitationConfig (bad content_type) returns 422."""
    client, workspace_id, server_id = server_with_citations

    # "binary" is not a valid Literal["json", "text"]
    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations",
        json={
            "tool_citations": {
                "web_search": {
                    "content_type": "binary",
                    "source_type": "web",
                    "content_field": None,
                    "mapping": {},
                }
            }
        },
    )
    assert resp.status_code == 422, resp.text


async def test_patch_forbidden_for_member(
    server_with_citations: tuple[httpx.AsyncClient, str, str],
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Non-admin workspace member is rejected with 403 or 401 when trying PATCH."""
    _, workspace_id, server_id = server_with_citations
    member_http, member_ws_id = member_client

    # Try against the admin's workspace (the member belongs to a different workspace).
    resp = await member_http.patch(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": {}},
    )
    # The member is not in this workspace at all, so the workspace check fires first → 403.
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# Catalog tool-citations endpoint tests
# ---------------------------------------------------------------------------


async def test_get_catalog_tool_citations_returns_defaults(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """The catalog endpoint returns the seeded tool_citations for a slug."""
    from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

    client, workspace_id = admin_client

    expected: dict[str, Any] = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "results",
            "mapping": {"snippet": "description"},
        },
    }
    repo = MCPCatalogConnectorRepository(db_session)
    await repo.upsert_by_slug(
        slug="webtools-citation-test",
        name="WebTools Test",
        description="t",
        provider="x",
        server_url="http://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_scope="org",
        tool_citations=expected,
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/webtools-citation-test/tool-citations"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "webtools-citation-test"
    assert body["tool_citations"] == expected


async def test_patch_tool_citations_works_on_org_wide_server(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """Admin can PATCH tool-citations on an org-wide server (owner_workspace_id=None)
    that is visible to the workspace via an enabled override.
    """
    from sqlalchemy import select as sa_select

    from cubebox.models import MCPServer, Membership, Workspace, WorkspaceMCPOverride

    client, workspace_id = admin_client

    # Resolve org_id and a valid user_id from the workspace's membership.
    ws_row = await db_session.get(Workspace, workspace_id)
    assert ws_row is not None
    org_id = ws_row.org_id

    mem_stmt = sa_select(Membership).where(Membership.workspace_id == workspace_id)
    mem_row = (await db_session.execute(mem_stmt)).scalars().first()
    assert mem_row is not None
    user_id = str(mem_row.user_id)

    # Seed an org-wide server directly in the DB.
    server = MCPServer(
        org_id=org_id,
        owner_workspace_id=None,  # ← org-wide
        name="org-wide-citation-test",
        server_url="http://localhost:9999/org-wide",
        server_url_hash="hash-org-wide-citation-test",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
        tools_cache=[{"name": "web_search", "description": "", "input_schema": {}}],
        tool_citations={},
        created_by_user_id=user_id,
    )
    db_session.add(server)
    await db_session.flush()

    # Add an enabled workspace override so the server is visible to this workspace.
    db_session.add(
        WorkspaceMCPOverride(
            org_id=org_id,
            workspace_id=workspace_id,
            mcp_server_id=server.id,
            enabled=True,
            updated_by_user_id=user_id,
        )
    )
    await db_session.commit()

    # PATCH must succeed (not 403) for an admin on a visible org-wide server.
    new_citations: dict[str, Any] = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "results",
            "mapping": {"snippet": "description"},
        }
    }
    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/servers/{server.id}/tool-citations",
        json={"tool_citations": new_citations},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_citations"] == new_citations


async def test_get_catalog_tool_citations_404_for_unknown_slug(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Unknown catalog slug returns 404."""
    client, workspace_id = admin_client
    resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/does-not-exist-9999/tool-citations"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Org-admin guard for org-wide server PATCH
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ws_admin_not_org_admin() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """A workspace admin who has only OrgRole.MEMBER at the org level.

    This fixture seeds a user with Role.ADMIN on their workspace but only
    OrgRole.MEMBER on their org — allowing us to verify the org-wide PATCH
    guard rejects them with 403.
    """
    from cubebox.auth.users import UserManager, _slugify_org_name
    from cubebox.db.engine import _build_database_url
    from cubebox.repositories import (
        MembershipRepository,
        OrganizationMembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )
    from tests.e2e.conftest import _lifespan_context, _login_and_attach, _make_test_app

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    email = f"ws-admin-only-{secrets.token_hex(4)}@example.com"
    password = secrets.token_urlsafe(16)
    workspace_id: str
    try:
        async with test_session_maker() as session:
            org_repo = OrganizationRepository(session)
            ws_repo = WorkspaceRepository(session)
            mem_repo = MembershipRepository(session)
            om_repo = OrganizationMembershipRepository(session)

            org_name = f"Org {email}"
            org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
            ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")
            workspace_id = ws.id

            from cubebox.models import User

            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)

            # Clean up bootstrap memberships from on_after_register.
            from sqlalchemy import delete as sa_delete

            from cubebox.models import Membership as MembershipModel
            from cubebox.models import OrganizationMembership

            await session.execute(
                sa_delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                )
            )
            await session.execute(
                sa_delete(MembershipModel).where(
                    MembershipModel.user_id == user.id,  # type: ignore[arg-type]
                    MembershipModel.workspace_id != ws.id,  # type: ignore[arg-type]
                )
            )
            await session.commit()

            # Grant workspace admin, but only OrgRole.MEMBER (not admin/owner).
            await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
            await om_repo.grant(user_id=user.id, org_id=org.id, role=OrgRole.MEMBER)
    finally:
        await test_engine.dispose()

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


@pytest.mark.asyncio
async def test_patch_tool_citations_org_wide_requires_org_admin(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    ws_admin_not_org_admin: tuple[httpx.AsyncClient, str],
) -> None:
    """A workspace admin (not org admin) cannot PATCH tool_citations on an org-wide server.

    The org-wide server must be visible to the ws-admin's workspace (via an enabled
    override) for the org-admin check to be reached. We seed the server + override
    via the admin_client's org, then share it to the ws-admin's workspace.
    """
    from sqlalchemy import select as sa_select

    from cubebox.models import Membership, Workspace, WorkspaceMCPOverride

    ws_admin_http, ws_admin_workspace_id = ws_admin_not_org_admin

    # Resolve the ws-admin's org so we can seed the org-wide server there.
    ws_row = await db_session.get(Workspace, ws_admin_workspace_id)
    assert ws_row is not None
    org_id = ws_row.org_id

    mem_stmt = sa_select(Membership).where(Membership.workspace_id == ws_admin_workspace_id)
    mem_row = (await db_session.execute(mem_stmt)).scalars().first()
    assert mem_row is not None
    user_id = str(mem_row.user_id)

    # Seed an org-wide server in the ws-admin's org.
    server = MCPServer(
        org_id=org_id,
        owner_workspace_id=None,  # org-wide
        name="org-wide-403-guard-test",
        server_url="http://localhost:9999/org-wide-403",
        server_url_hash="hash-org-wide-403-guard-test",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
        tools_cache=[{"name": "list_workers", "description": "", "input_schema": {}}],
        tool_citations={},
        created_by_user_id=user_id,
    )
    db_session.add(server)
    await db_session.flush()

    # Enable an override so the server is visible to the ws-admin's workspace.
    db_session.add(
        WorkspaceMCPOverride(
            org_id=org_id,
            workspace_id=ws_admin_workspace_id,
            mcp_server_id=server.id,
            enabled=True,
            updated_by_user_id=user_id,
        )
    )
    await db_session.commit()

    # PATCH by a workspace admin who is NOT org-admin must be rejected with 403.
    resp = await ws_admin_http.patch(
        f"/api/v1/ws/{ws_admin_workspace_id}/mcp/servers/{server.id}/tool-citations",
        json={
            "tool_citations": {
                "list_workers": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {},
                }
            }
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "mcp_org_wide_citations_require_org_admin"
