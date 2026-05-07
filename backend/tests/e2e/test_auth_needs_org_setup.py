"""E2E: /auth/me returns needs_org_setup flag."""

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_authenticated_user_with_org_membership(memory_client):
    resp = await memory_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["needs_org_setup"] is False
