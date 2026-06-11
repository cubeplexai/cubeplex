"""End-to-end smoke that ensures all modules built so far are importable
and the worker round-trips a real conversation."""

import pytest

from cubebox.db.engine import async_session_maker
from cubebox.repositories.conversation_chunk import ConversationChunkRepository
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.worker import EmbeddingWorker


class _Det(EmbeddingProvider):
    def __init__(self) -> None:
        self.dimensions = 1024
        self._model = "det"
        self._base_url = "https://det.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "det@det.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 / (i + 1)] * self.dimensions for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_worker_end_to_end(
    seeded_conversation: tuple[str, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        await EmbeddingJobRepository(s).enqueue(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            conversation_id=conv_id,
        )
    worker = EmbeddingWorker(_Det())
    await worker._claim_one()
    async with async_session_maker() as s:
        n = await ConversationChunkRepository(s).count_for_conversation(conv_id)
    assert n > 0
