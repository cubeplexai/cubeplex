"""GET /api/v1/workspaces includes last_activity_at."""

import pytest

pytestmark = pytest.mark.e2e


async def test_workspace_response_has_last_activity_at(admin_client):
    client, _workspace_id = admin_client
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "last_activity_at" in data[0]
    # ISO-8601 string or None
    val = data[0]["last_activity_at"]
    assert val is None or isinstance(val, str)


async def test_last_activity_at_reflects_conversation_updated_at(admin_client):
    """When a conversation exists, last_activity_at returns its updated_at (ISO)."""
    client, workspace_id = admin_client

    # Create a conversation in the workspace (title is a query param)
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations",
        params={"title": "t"},
    )
    assert resp.status_code in (200, 201), resp.text

    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200
    data = resp.json()
    my = next(w for w in data if w["id"] == workspace_id)
    assert my["last_activity_at"] is not None
    # Must include a UTC offset per feedback_timestamp_handling memory
    assert "+" in my["last_activity_at"] or my["last_activity_at"].endswith("Z")
