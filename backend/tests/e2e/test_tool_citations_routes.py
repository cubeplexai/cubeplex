"""End-to-end: GET/PATCH /ws/{wsId}/mcp/servers/{id}/tool-citations."""

from __future__ import annotations

from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPServer

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


async def test_get_catalog_tool_citations_404_for_unknown_slug(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Unknown catalog slug returns 404."""
    client, workspace_id = admin_client
    resp = await client.get(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/does-not-exist-9999/tool-citations"
    )
    assert resp.status_code == 404
