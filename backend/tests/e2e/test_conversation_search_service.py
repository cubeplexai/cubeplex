"""End-to-end smoke that ensures all modules built so far are importable
and the worker round-trips a real conversation."""

import pytest

from cubeplex.db.engine import async_session_maker
from cubeplex.repositories.conversation_chunk import ConversationChunkRepository
from cubeplex.repositories.embedding_job import EmbeddingJobRepository
from cubeplex.services.conversation_search.embedding import EmbeddingProvider
from cubeplex.services.conversation_search.worker import EmbeddingWorker


class _Det(EmbeddingProvider):
    def __init__(self) -> None:
        self.vector_dim = 1024
        self._model = "det"
        self._base_url = "https://det.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "det@det.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 / (i + 1)] * self.vector_dim for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_worker_end_to_end(
    seeded_conversation: tuple[str, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    worker = EmbeddingWorker(_Det())
    await worker._claim_one()
    async with async_session_maker() as s:
        repo = ConversationChunkRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        n = await repo.count_for_conversation(conv_id)
    assert n > 0


class _KeywordEmbedder(EmbeddingProvider):
    """Maps 'docling' text → unit vector; everything else → zero vector.

    Makes the vector leg's cosine score deterministic so the test asserts
    against the fused result without needing a real embedding endpoint.
    """

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


@pytest.mark.asyncio
async def test_search_returns_seeded_conversation(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """After indexing, a search for 'docling' returns the seeded conversation."""
    from cubeplex.services.conversation_search.service import ConversationSearchService

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(_KeywordEmbedder())._claim_one()
    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _KeywordEmbedder())
        resp = await svc.search(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            q="docling",
            limit=8,
        )
    assert any(r.conversation_id == conv_id for r in resp.results)
    hit = next(r for r in resp.results if r.conversation_id == conv_id)
    assert hit.matched_message_seq is not None
    assert hit.matched_at is not None and "+00:00" in hit.matched_at
    assert hit.title == "seed"


@pytest.mark.asyncio
async def test_search_excludes_soft_deleted_conversation(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Soft-deleted conversations must not appear in search results."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    from cubeplex.models.conversation import Conversation
    from cubeplex.services.conversation_search.service import ConversationSearchService

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(_KeywordEmbedder())._claim_one()
    # Soft-delete the conversation after indexing.
    async with async_session_maker() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await s.commit()
    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _KeywordEmbedder())
        resp = await svc.search(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            q="docling",
            limit=8,
        )
    assert not any(r.conversation_id == conv_id for r in resp.results)


@pytest.mark.asyncio
async def test_search_legs_run_concurrently_without_session_race(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Both legs fire under asyncio.gather without raising MissingGreenlet.

    Pre-fix, both _lexical_leg and _vector_leg shared the service-level
    AsyncSession, which SQLAlchemy treats as a single-task unit of work. The
    overlap raised greenlet / transaction errors that ``return_exceptions=True``
    silently swallowed as empty legs. After the fix, each leg opens its own
    ``async_session_maker()`` and both legs see real data.
    """
    from cubeplex.services.conversation_search.service import ConversationSearchService

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(_KeywordEmbedder())._claim_one()
    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _KeywordEmbedder())
        resp = await svc.search(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            q="docling",
            limit=8,
        )
    # Both legs returned hits — pre-fix, the race made at least one leg empty.
    assert resp.lexical_count > 0
    assert resp.vector_count > 0
    assert resp.fused_count > 0


@pytest.mark.asyncio
async def test_vector_leg_filters_by_embed_model(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Vector leg returns only chunks embedded with the current provider's model.

    Simulates an operator rotation of ``search.embedding.model`` /
    ``base_url`` (same dimension). After indexing with model A, swap the
    rows' ``embed_model`` to a stale tag and search with model B — the
    vector leg must report zero hits while the lexical leg (which is
    model-agnostic) still finds the seeded conversation.
    """
    from sqlalchemy import text as sql_text

    from cubeplex.services.conversation_search.service import ConversationSearchService

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(_KeywordEmbedder())._claim_one()

    # Rotate the stored embed_model out from under the live provider.
    async with async_session_maker() as s:
        await s.execute(
            sql_text(
                "UPDATE conversation_chunks SET embed_model = :stale WHERE conversation_id = :cid"
            ),
            {"stale": "stale@old.local", "cid": conv_id},
        )
        await s.commit()

    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _KeywordEmbedder())
        resp = await svc.search(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            q="docling",
            limit=8,
        )
    # Vector leg saw zero matching-model chunks; lexical leg is unaffected.
    assert resp.vector_count == 0
    assert resp.lexical_count > 0


@pytest.mark.asyncio
async def test_search_runs_lexical_only_when_provider_is_none(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Service(provider=None) runs the lexical leg only.

    Worker writes chunks with embedding=NULL; service must return real
    lexical hits and vector_count=0 (vector leg short-circuits).
    """
    from cubeplex.services.conversation_search.service import ConversationSearchService
    from cubeplex.services.conversation_search.worker import EmbeddingWorker

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(s, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        await repo.enqueue(conversation_id=conv_id)
    await EmbeddingWorker(None)._claim_one()
    async with async_session_maker() as s:
        svc = ConversationSearchService(s, None)
        resp = await svc.search(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            q="docling",
            limit=8,
        )
    assert resp.vector_count == 0
    assert resp.lexical_count > 0
    assert any(r.conversation_id == conv_id for r in resp.results)


@pytest.mark.asyncio
async def test_search_rejects_control_characters(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Query with embedded control characters raises InvalidInputError."""
    from cubeplex.api.exceptions import InvalidInputError
    from cubeplex.services.conversation_search.service import ConversationSearchService

    org_id, ws_id, user_id, _conv_id = seeded_conversation
    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _KeywordEmbedder())
        with pytest.raises(InvalidInputError):
            await svc.search(
                org_id=org_id,
                workspace_id=ws_id,
                user_id=user_id,
                q="doc\x00ling",
                limit=8,
            )
