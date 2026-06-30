"""E2E: GET /api/v1/system/info — public, mode-aware."""

import pytest

pytestmark = pytest.mark.e2e


async def test_system_info_public_no_auth(unauthenticated_memory_client):
    resp = await unauthenticated_memory_client.get("/api/v1/system/info")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["deployment_mode"] in ("single_tenant", "multi_tenant")
    assert isinstance(data["version"], str) and data["version"]
    assert isinstance(data["sandbox_enabled"], bool)
