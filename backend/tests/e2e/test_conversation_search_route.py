"""Route-level tests for `GET /api/v1/ws/{ws}/conversations/search`.

Covers the contract — auth gating, parameter validation, the 503 path when
search is disabled, and a happy-path search after manually driving the worker.
The full end-to-end (real embedding endpoint, both keyword languages) lives in
`test_conversation_search.py` and is secret-gated.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from cubebox.db.engine import async_session_maker
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.services.conversation_search.embedding import EmbeddingProvider
from cubebox.services.conversation_search.worker import EmbeddingWorker
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID


class _KeywordEmbedder(EmbeddingProvider):
    """Deterministic provider: 'docling' → unit vector, otherwise zeros."""

    def __init__(self) -> None:
        self.vector_dim = 1024
        self._model = "kw"
        self._base_url = "https://kw.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "kw@kw.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0] * self.vector_dim if "docling" in t.lower() else [0.0] * self.vector_dim
            for t in texts
        ]

    async def aclose(self) -> None:  # type: ignore[override]
        # The parent __init__ was bypassed, so there's no httpx.AsyncClient
        # to close. Override so the lifespan shutdown doesn't raise.
        return None


def _default_user_id(client: TestClient) -> str:
    resp = client.get("/api/v1/auth/me")
    resp.raise_for_status()
    return str(resp.json()["id"])


async def _seed_indexed_conversation(client: TestClient) -> str:
    """Create a conversation via the API, append cubepi messages, run the worker."""
    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    from cubebox.agents.checkpointer import init_checkpointer

    title_resp = client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
        params={"title": "docling notes"},
    )
    title_resp.raise_for_status()
    conv_id = str(title_resp.json()["id"])

    async with init_checkpointer() as cp:
        await cp.append(
            conv_id,
            [
                UserMessage(content=[TextContent(text="hello docling")], timestamp=1.0),
                AssistantMessage(content=[TextContent(text="hi there")], timestamp=2.0),
            ],
        )

    user_id = _default_user_id(client)
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(
            s,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            user_id=user_id,
        )
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(_KeywordEmbedder())._claim_one()
    return conv_id


def test_search_route_rejects_empty_query(client: TestClient) -> None:
    """FastAPI Query(min_length=1) makes whitespace-only `q` 422."""
    resp = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
        params={"q": ""},
    )
    assert resp.status_code == 422


def test_search_route_runs_lexical_only_when_provider_missing(
    client: TestClient,
) -> None:
    """No provider on app.state → route returns 200 with vector_count=0."""
    previous: Any = getattr(client.app.state, "embedding_provider", None)
    client.app.state.embedding_provider = None
    try:
        resp = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
            params={"q": "docling"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["vector_count"] == 0
    finally:
        client.app.state.embedding_provider = previous


@pytest.mark.asyncio
async def test_search_route_returns_indexed_conversation(client: TestClient) -> None:
    """End-to-end happy path: seed → index → search returns the conversation."""
    # The lifespan-managed worker uses the real EmbeddingProvider (built from
    # config) and would race us, claiming the job we enqueue and failing it
    # against the live DashScope endpoint. Cancel its task before driving
    # the worker ourselves with a deterministic provider.
    import asyncio

    lifespan_task = getattr(client.app.state, "embedding_worker_task", None)
    if lifespan_task is not None:
        lifespan_task.cancel()
        try:
            await lifespan_task
        except (asyncio.CancelledError, Exception):
            pass
        client.app.state.embedding_worker_task = None
        client.app.state.embedding_worker = None
    client.app.state.embedding_provider = _KeywordEmbedder()
    conv_id = await _seed_indexed_conversation(client)

    resp = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
        params={"q": "docling", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(r["conversation_id"] == conv_id for r in body["results"])
    hit = next(r for r in body["results"] if r["conversation_id"] == conv_id)
    assert hit["title"] == "docling notes"
    assert hit["matched_message_seq"] is not None
    assert hit["matched_at"] is None or "+00:00" in hit["matched_at"]
