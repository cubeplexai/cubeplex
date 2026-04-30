"""Smoke tests for the reference MCP-like server fixture."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from tests.fixtures.reference_mcp_server import ReferenceMCPServer

pytest_plugins = ["tests.fixtures.reference_mcp_server"]


async def test_fixture_serves_tools_list(
    reference_mcp_server: Callable[..., AbstractContextManager[ReferenceMCPServer]],
) -> None:
    with reference_mcp_server(auth_mode="none") as server:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{server.base_url}/mcp/tools/list")

    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert any(tool["name"] == "echo" for tool in data["tools"])
