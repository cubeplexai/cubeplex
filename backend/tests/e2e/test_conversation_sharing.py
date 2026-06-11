"""E2E: conversation sharing create → list → public read → revoke flow."""

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_share_lifecycle(authenticated_client) -> None:  # type: ignore[no-untyped-def]
    """Create conversation → share → read public → revoke → 404."""
    client, workspace_id = authenticated_client

    # Create a conversation
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations",
        params={"title": "Share test"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    # Create a share
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/{conv_id}/shares",
    )
    assert resp.status_code == 201
    share = resp.json()
    share_id = share["id"]
    assert share_id.startswith("shr-")
    assert share["title"] == "Share test"
    assert share["is_active"] is True
    assert "/share/" in share["url"]

    # List shares for this conversation
    resp = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conv_id}/shares",
    )
    assert resp.status_code == 200
    shares = resp.json()
    assert len(shares) == 1
    assert shares[0]["id"] == share_id

    # List all shares in workspace
    resp = await client.get(f"/api/v1/ws/{workspace_id}/shares")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1

    # Public read (no auth required — same client works; public route has no auth guard)
    resp = await client.get(f"/api/v1/public/shares/{share_id}")
    assert resp.status_code == 200
    public = resp.json()
    assert public["title"] == "Share test"
    assert "messages" in public
    assert "artifacts" in public

    # Revoke
    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/shares/{share_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Public read after revoke → 404
    resp = await client.get(f"/api/v1/public/shares/{share_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_share_nonexistent_conversation(authenticated_client) -> None:  # type: ignore[no-untyped-def]
    """Sharing a conversation that doesn't exist → 404."""
    client, workspace_id = authenticated_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/conv-doesnotexist/shares",
    )
    assert resp.status_code == 404
