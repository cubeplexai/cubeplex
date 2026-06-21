"""E2E tests for Conversations API

Tests CRUD operations and the message streaming endpoint.
"""

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.repositories import ConversationRepository
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)
from tests.e2e.helpers import parse_sse_stream

pytestmark = pytest.mark.e2e


class TestConversationsCRUD:
    """Conversations CRUD endpoint tests."""

    def test_create_conversation(self, client: TestClient) -> None:
        """Create a conversation and verify the response."""
        response = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Test Conversation"}
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
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Get Test"}
        )
        conversation_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["id"] == conversation_id
        assert data["title"] == "Get Test"

    def test_get_conversation_not_found(self, client: TestClient) -> None:
        """Get a non-existent conversation returns 404."""
        response = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id")
        assert response.status_code == 404

    def test_list_conversations(self, client: TestClient) -> None:
        """List conversations returns paginated results."""
        # Create two conversations
        client.post(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "List Test 1"})
        client.post(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "List Test 2"})

        response = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"limit": 10, "offset": 0}
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
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Original Title"}
        )
        conversation_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}",
            params={"title": "Updated Title"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["title"] == "Updated Title"

    def test_update_conversation_not_found(self, client: TestClient) -> None:
        """Update a non-existent conversation returns 404."""
        response = client.patch(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id",
            params={"title": "New Title"},
        )
        assert response.status_code == 404

    def test_delete_conversation(self, client: TestClient) -> None:
        """Delete a conversation and verify it's gone."""
        create_resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "To Delete"}
        )
        conversation_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}")
        assert get_resp.status_code == 404

    def test_delete_conversation_not_found(self, client: TestClient) -> None:
        """Delete a non-existent conversation returns 404."""
        response = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id")
        assert response.status_code == 404

    def test_delete_conversation_with_billing_event(self, client: TestClient) -> None:
        """Regression: delete must succeed even with billing rows referencing it.

        Before soft delete, the ON DELETE NO ACTION FK on
        billing_events.conversation_id raised IntegrityError. With soft
        delete the row stays (deleted_at is stamped), so the FK target
        remains valid and cost history survives.
        """
        import asyncio

        from sqlalchemy import select, text

        from cubebox import db as _cubebox_db
        from cubebox.models.billing import BillingEvent

        create_resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "with billing"}
        )
        conversation_id = create_resp.json()["id"]

        async def _seed_and_check() -> str:
            async with _cubebox_db.async_session_maker() as session:
                user_id = (
                    await session.execute(
                        text("SELECT creator_user_id FROM conversations WHERE id=:id"),
                        {"id": conversation_id},
                    )  # noqa: E501
                ).scalar_one()
                event = BillingEvent(
                    org_id="org-00000000000000",
                    workspace_id=DEFAULT_WS_ID,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="llm_call",
                    status="success",
                )
                session.add(event)
                await session.commit()
                return event.id

        event_id = asyncio.get_event_loop().run_until_complete(_seed_and_check())

        delete_resp = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}")
        assert delete_resp.status_code == 204

        # Conversation hidden from API
        assert (
            client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}").status_code
            == 404
        )

        async def _verify_billing_intact() -> str | None:
            async with _cubebox_db.async_session_maker() as session:
                row = (
                    await session.execute(select(BillingEvent).where(BillingEvent.id == event_id))
                ).scalar_one()
                return row.conversation_id

        retained = asyncio.get_event_loop().run_until_complete(_verify_billing_intact())
        assert retained == conversation_id, (
            f"billing_event should still reference the conversation, got {retained!r}"
        )

    def test_artifacts_hidden_for_soft_deleted_conversation(self, client: TestClient) -> None:
        """Regression: artifact routes must 404 once the parent conversation is gone.

        Before this fix, ``ArtifactRepository`` only scoped by org/workspace,
        so a stale conversation URL kept exposing the child rows through
        ``GET .../artifacts`` even after the conversation was soft-deleted.
        """
        import asyncio

        from cubebox import db as _cubebox_db
        from cubebox.models.artifact import Artifact

        create_resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "for artifacts"}
        )
        conversation_id = create_resp.json()["id"]

        async def _seed_artifact() -> str:
            async with _cubebox_db.async_session_maker() as session:
                art = Artifact(
                    org_id="org-00000000000000",
                    workspace_id=DEFAULT_WS_ID,
                    conversation_id=conversation_id,
                    name="report",
                    artifact_type="code",
                    path="/tmp/report",
                )
                session.add(art)
                await session.commit()
                return art.id

        artifact_id = asyncio.get_event_loop().run_until_complete(_seed_artifact())

        # Visible before delete
        list_resp = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/artifacts"
        )
        assert list_resp.status_code == 200
        assert artifact_id in {a["id"] for a in list_resp.json()["artifacts"]}

        # Soft-delete the parent
        del_resp = client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}")
        assert del_resp.status_code == 204

        # Every artifact endpoint now refuses the request
        for url in (
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/artifacts",
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/artifacts/{artifact_id}",
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/artifacts/{artifact_id}/versions",
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/artifacts/{artifact_id}/download",
        ):
            resp = client.get(url)
            assert resp.status_code == 404, f"{url} should 404, got {resp.status_code}"


class TestConversationsMessages:
    """Conversation message listing tests."""

    def test_list_messages_empty(self, client: TestClient) -> None:
        """List messages for a new conversation returns empty list."""
        create_resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Messages Test"}
        )
        conversation_id = create_resp.json()["id"]

        response = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["messages"] == []
        assert data["total"] == 0

    def test_list_messages_not_found(self, client: TestClient) -> None:
        """List messages for non-existent conversation returns 404."""
        response = client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id/messages")
        assert response.status_code == 404


@pytest.mark.slow
class TestSendMessage:
    """Message send (SSE streaming) tests — requires real LLM API access."""

    @pytest.mark.asyncio
    async def test_send_message_streams_events(self, async_client: httpx.AsyncClient) -> None:
        """Send a message and verify SSE event stream structure."""
        create_resp = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Stream Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        async with async_client.stream(
            "POST",
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages",
            json={"content": "Say 'hello' in one word."},
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
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
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Persist Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        async with async_client.stream(
            "POST",
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages",
            json={"content": "What is 1+1?"},
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
        ) as response:
            assert response.status_code == 200
            # Consume the stream
            await parse_sse_stream(response.aiter_bytes())

        msgs_resp = await async_client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages"
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
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "Empty Content Test"}
        )
        assert create_resp.status_code == 201
        conversation_id = create_resp.json()["id"]

        response = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages",
            json={"content": ""},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_send_message_to_nonexistent_conversation(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """Sending to non-existent conversation returns 404."""
        response = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id/messages",
            json={"content": "Hello"},
        )
        assert response.status_code == 404


@pytest_asyncio.fixture
async def _default_user_id() -> str:
    """Resolve the seeded default user's id for repo-scoped tests."""
    from sqlalchemy import select

    from cubebox.models import User

    await _ensure_default_user_and_membership()
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            user = (
                await session.execute(select(User).where(User.email == DEFAULT_TEST_EMAIL))
            ).scalar_one()
            return user.id
    finally:
        await engine.dispose()


class TestConversationModelSetting:
    """Persistence + serialization of the per-conversation model setting."""

    @pytest.mark.asyncio
    async def test_mark_active_persists_model_setting(self, _default_user_id: str) -> None:
        """mark_active(model_setting=...) stores the key + thinking; a plain
        mark_active afterwards leaves them untouched."""
        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                repo = ConversationRepository(
                    session,
                    org_id=DEFAULT_ORG_ID,
                    workspace_id=DEFAULT_WS_ID,
                    user_id=_default_user_id,
                )
                conv = await repo.create(title="model-setting", draft=True)

                await repo.mark_active(conv.id, model_setting=("pro", "high"))
                await session.refresh(conv)
                assert conv.model_key == "pro"
                assert conv.thinking == "high"

                # A timestamp-only mark_active (the install-fallback caller)
                # must NOT clobber the previously stored model setting.
                await repo.mark_active(conv.id)
                await session.refresh(conv)
                assert conv.model_key == "pro"
                assert conv.thinking == "high"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_conversation_returns_model_setting(
        self, async_client: httpx.AsyncClient, _default_user_id: str
    ) -> None:
        """GET /conversations/{id} surfaces model_key + thinking."""
        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                repo = ConversationRepository(
                    session,
                    org_id=DEFAULT_ORG_ID,
                    workspace_id=DEFAULT_WS_ID,
                    user_id=_default_user_id,
                )
                conv = await repo.create(title="serialize-model-setting")
                await repo.mark_active(conv.id, model_setting=("ultra", "low"))
                conv_id = conv.id
        finally:
            await engine.dispose()

        resp = await async_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["model_key"] == "ultra"
        assert data["thinking"] == "low"
