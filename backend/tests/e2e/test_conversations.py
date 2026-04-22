"""E2E tests for Conversations API

Tests CRUD operations and the message streaming endpoint.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.e2e.helpers import parse_sse_stream

pytestmark = pytest.mark.e2e


class TestConversationsCRUD:
    """Conversations CRUD endpoint tests."""

    def test_create_conversation(self, client: TestClient) -> None:
        """Create a conversation and verify the response."""
        response = client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Test Conversation"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Conversation"
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_get_conversation(self, client: TestClient) -> None:
        """Create then retrieve a conversation."""
        create_resp = client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Get Test"}
        )
        conversation_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/ws/default-ws/conversations/{conversation_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["id"] == conversation_id
        assert data["title"] == "Get Test"

    def test_get_conversation_not_found(self, client: TestClient) -> None:
        """Get a non-existent conversation returns 404."""
        response = client.get("/api/v1/ws/default-ws/conversations/nonexistent-id")
        assert response.status_code == 404

    def test_list_conversations(self, client: TestClient) -> None:
        """List conversations returns paginated results."""
        # Create two conversations
        client.post("/api/v1/ws/default-ws/conversations", params={"title": "List Test 1"})
        client.post("/api/v1/ws/default-ws/conversations", params={"title": "List Test 2"})

        response = client.get(
            "/api/v1/ws/default-ws/conversations", params={"limit": 10, "offset": 0}
        )
        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
        assert "total" in data
        assert data["total"] >= 2
        assert isinstance(data["conversations"], list)

    def test_update_conversation_title(self, client: TestClient) -> None:
        """Update conversation title."""
        create_resp = client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Original Title"}
        )
        conversation_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/api/v1/ws/default-ws/conversations/{conversation_id}",
            params={"title": "Updated Title"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["title"] == "Updated Title"

    def test_update_conversation_not_found(self, client: TestClient) -> None:
        """Update a non-existent conversation returns 404."""
        response = client.patch(
            "/api/v1/ws/default-ws/conversations/nonexistent-id",
            params={"title": "New Title"},
        )
        assert response.status_code == 404

    def test_delete_conversation(self, client: TestClient) -> None:
        """Delete a conversation and verify it's gone."""
        create_resp = client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "To Delete"}
        )
        conversation_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/api/v1/ws/default-ws/conversations/{conversation_id}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/api/v1/ws/default-ws/conversations/{conversation_id}")
        assert get_resp.status_code == 404

    def test_delete_conversation_not_found(self, client: TestClient) -> None:
        """Delete a non-existent conversation returns 404."""
        response = client.delete("/api/v1/ws/default-ws/conversations/nonexistent-id")
        assert response.status_code == 404


@pytest.mark.e2e
class TestConversationsMessages:
    """Conversation message listing tests."""

    def test_list_messages_empty(self, client: TestClient) -> None:
        """List messages for a new conversation returns empty list."""
        create_resp = client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Messages Test"}
        )
        conversation_id = create_resp.json()["id"]

        response = client.get(f"/api/v1/ws/default-ws/conversations/{conversation_id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert data["messages"] == []
        assert data["total"] == 0

    def test_list_messages_not_found(self, client: TestClient) -> None:
        """List messages for non-existent conversation returns 404."""
        response = client.get("/api/v1/ws/default-ws/conversations/nonexistent-id/messages")
        assert response.status_code == 404


@pytest.mark.e2e
@pytest.mark.slow
class TestSendMessage:
    """Message send (SSE streaming) tests — requires real LLM API access."""

    @pytest.mark.asyncio
    async def test_send_message_streams_events(self, async_client: httpx.AsyncClient) -> None:
        """Send a message and verify SSE event stream structure."""
        create_resp = await async_client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Stream Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        async with async_client.stream(
            "POST",
            f"/api/v1/ws/default-ws/conversations/{conversation_id}/messages",
            json={"content": "Say 'hello' in one word."},
        ) as response:
            assert response.status_code == 200
            events = await parse_sse_stream(response.aiter_bytes())

        event_types = [e.type for e in events]
        assert "text_delta" in event_types
        assert "done" in event_types
        assert "error" not in event_types
        assert events[-1].type == "done"

    @pytest.mark.asyncio
    async def test_send_message_saves_to_db(self, async_client: httpx.AsyncClient) -> None:
        """After streaming, messages are persisted in the DB."""
        create_resp = await async_client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Persist Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        async with async_client.stream(
            "POST",
            f"/api/v1/ws/default-ws/conversations/{conversation_id}/messages",
            json={"content": "What is 1+1?"},
        ) as response:
            assert response.status_code == 200
            # Consume the stream
            await parse_sse_stream(response.aiter_bytes())

        msgs_resp = await async_client.get(
            f"/api/v1/ws/default-ws/conversations/{conversation_id}/messages"
        )
        assert msgs_resp.status_code == 200
        data = msgs_resp.json()
        assert data["total"] >= 2  # user + assistant

        roles = [m["role"] for m in data["messages"]]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_send_message_empty_content(self, async_client: httpx.AsyncClient) -> None:
        """Empty message content returns 400."""
        create_resp = await async_client.post(
            "/api/v1/ws/default-ws/conversations", params={"title": "Empty Content Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        response = await async_client.post(
            f"/api/v1/ws/default-ws/conversations/{conversation_id}/messages",
            json={"content": ""},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_send_message_to_nonexistent_conversation(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """Sending to non-existent conversation returns 404."""
        response = await async_client.post(
            "/api/v1/ws/default-ws/conversations/nonexistent-id/messages",
            json={"content": "Hello"},
        )
        assert response.status_code == 404
