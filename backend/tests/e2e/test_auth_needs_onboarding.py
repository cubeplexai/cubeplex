"""E2E: /auth/me returns needs_onboarding flag."""

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_authenticated_user_with_workspace_membership(memory_client):
    # memory_client fixture grants a workspace membership → onboarding complete.
    resp = await memory_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["needs_onboarding"] is False
