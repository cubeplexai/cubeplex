"""E2E: GET /api/v1/system/info — public, mode-aware."""

import pytest

pytestmark = pytest.mark.e2e


async def test_system_info_public_no_auth(unauthenticated_memory_client):
    resp = await unauthenticated_memory_client.get("/api/v1/system/info")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["deployment_mode"] in ("single_tenant", "multi_tenant")
    assert isinstance(data["version"], str) and data["version"]
    assert isinstance(data["needs_org_setup"], bool)


async def test_system_info_needs_setup_false_when_orgs_exist(memory_client):
    # memory_client fixture creates a default user → at least one org exists.
    resp = await memory_client.get("/api/v1/system/info")
    assert resp.status_code == 200
    assert resp.json()["needs_org_setup"] is False
