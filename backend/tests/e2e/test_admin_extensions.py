"""E2E: GET /api/v1/admin/_extensions/manifest in CE-only deployment returns []."""

import httpx
import pytest


@pytest.mark.asyncio
async def test_admin_manifest_empty_in_ce(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws_id = admin_client
    resp = await client.get("/api/v1/admin/_extensions/manifest")
    assert resp.status_code == 200
    assert resp.json() == []
