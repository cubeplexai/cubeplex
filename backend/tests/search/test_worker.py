"""Integration test for EmbeddingWorker — real Postgres, fake embedding provider."""

import pytest

from cubebox.db.engine import async_session_maker
from cubebox.repositories.conversation_chunk import ConversationChunkRepository
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.worker import EmbeddingWorker


class _FakeProvider(EmbeddingProvider):
    def __init__(self) -> None:
        # Bypass real init; we never call HTTP.
        self.dimensions = 1024
        self._model = "fake"
        self._base_url = "https://fake.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "fake@fake.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 * (i + 1)] * self.dimensions for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_worker_processes_job_for_seeded_conversation(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as session:
        await EmbeddingJobRepository(session).enqueue(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            conversation_id=conv_id,
        )
    worker = EmbeddingWorker(_FakeProvider())
    job = await worker._claim_one()
    assert job is not None
    async with async_session_maker() as session:
        n = await ConversationChunkRepository(session).count_for_conversation(conv_id)
    assert n > 0
