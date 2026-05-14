"""End-to-end: catalog install + refresh orphan cleanup.

NOTE on scope: this file covers scenarios 1 (install propagation) and 4
(refresh orphan cleanup) only. The "agent-run produces citation events" and
"PATCH changes next agent run" scenarios from the plan require a fake MCP HTTP
server fixture that doesn't exist in this codebase. Those flows are covered
piece-by-piece in unit tests:

- CitationMiddleware behavior with non-empty citation_configs:
  tests/unit/test_citation.py
- Loader returns (tools, citation_configs) keyed by namespaced names:
  tests/unit/mcp/test_namespace_and_citations.py
- run_manager threads the dict into CitationMiddleware:
  tests/unit/mcp/test_namespace_and_citations.py + Task 7's mechanical wiring

The full agent-run E2E is deferred until a fake MCP HTTP server fixture lands
(tracked as follow-up work).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPServer
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

# ---------------------------------------------------------------------------
# Scenario 1: catalog install carries tool_citations through to server row
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_discover_tools")
async def test_install_from_catalog_carries_tool_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """Installing from a catalog with non-empty tool_citations produces a server
    row whose tool_citations field matches the catalog row.
    """
    client, workspace_id = admin_client

    expected_citations: dict[str, Any] = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "results",
            "mapping": {"snippet": "description"},
        },
    }

    # Seed a catalog connector with tool_citations.
    repo = MCPCatalogConnectorRepository(db_session)
    catalog = await repo.upsert_by_slug(
        slug="webtools-install-flow",
        name="WebTools Flow",
        description="Test catalog with citation defaults.",
        provider="test",
        server_url="http://127.0.0.1:9/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_scope="workspace",
        static_form_fields=[
            {
                "name": "token",
                "label": "Token",
                "secret": True,
                "placeholder": "x",
                "helper_url": None,
            }
        ],
        static_auth_header_template="Bearer {token}",
        tool_citations=expected_citations,
    )
    await db_session.commit()

    # Install into this workspace via the workspace-scoped catalog install endpoint.
    install_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{catalog.id}/install",
        json={"auth_method": "static", "credential_plaintext": "test-token"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]
    assert install_id

    # Fetch the resulting tool_citations via the GET endpoint.
    tc_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{install_id}/tool-citations")
    assert tc_resp.status_code == 200, tc_resp.text
    body = tc_resp.json()
    assert body["tool_citations"] == expected_citations


# ---------------------------------------------------------------------------
# Scenario 4: refresh-tools strips orphan citation keys and records notice
# ---------------------------------------------------------------------------


async def _create_workspace_server(
    client: httpx.AsyncClient,
    workspace_id: str,
    *,
    name: str,
) -> str:
    """POST a workspace-owned MCP server; return its id."""
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/servers",
        json={
            "name": name,
            "server_url": "http://127.0.0.1:9/orphan-test",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
        },
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


async def test_refresh_tools_strips_orphan_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """After /refresh-tools removes a tool from tools_cache, its tool_citations
    key is also stripped and a notice is recorded in last_error.
    """
    client, workspace_id = admin_client

    # 1. Create a workspace-owned server.
    server_id = await _create_workspace_server(client, workspace_id, name="refresh-orphan-test")

    # 2. Seed tools_cache + tool_citations directly via DB.
    #    Both web_search and old_tool are present in both caches.
    valid_citation: dict[str, Any] = {
        "content_type": "json",
        "source_type": "web",
        "content_field": None,
        "mapping": {"snippet": "s"},
    }
    await _seed_tools_and_citations(
        db_session,
        server_id,
        tools_cache=[
            {"name": "web_search", "description": "", "input_schema": {}},
            {"name": "old_tool", "description": "", "input_schema": {}},
        ],
        tool_citations={
            "web_search": valid_citation,
            "old_tool": valid_citation,
        },
    )

    # 3. POST /refresh-tools with discovery patched to return only web_search.
    with patch(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        new=AsyncMock(
            return_value=(
                True,
                [{"name": "web_search", "description": "", "input_schema": {}}],
                None,
            )
        ),
    ):
        refresh_resp = await client.post(
            f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/refresh-tools",
        )
        assert refresh_resp.status_code == 200, refresh_resp.text

    # 4. Read back via the tool-citations GET endpoint.
    tc_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations")
    assert tc_resp.status_code == 200, tc_resp.text
    body = tc_resp.json()
    assert "old_tool" not in body["tool_citations"], (
        "old_tool citation should have been stripped after refresh"
    )
    assert "web_search" in body["tool_citations"], (
        "web_search citation must survive when the tool is still present"
    )

    # 5. Read the server row directly to confirm last_error mentions the removed tool.
    await db_session.refresh(await db_session.get(MCPServer, server_id))  # type: ignore[arg-type]
    server = await db_session.get(MCPServer, server_id)
    assert server is not None
    assert server.last_error is not None, "last_error must record the orphan notice"
    assert "old_tool" in server.last_error, (
        f"Expected 'old_tool' in last_error, got: {server.last_error!r}"
    )
