"""Integration test for EmbeddingWorker — real Postgres, fake embedding provider."""

import pytest
from sqlalchemy import text as sql_text

from cubebox.db.engine import async_session_maker
from cubebox.repositories.conversation_chunk import ConversationChunkRepository
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.services.conversation_search.embedding import EmbeddingProvider
from cubebox.services.conversation_search.worker import EmbeddingWorker


class _FakeProvider(EmbeddingProvider):
    def __init__(self) -> None:
        # Bypass real init; we never call HTTP.
        self.vector_dim = 1024
        self._model = "fake"
        self._base_url = "https://fake.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "fake@fake.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 * (i + 1)] * self.vector_dim for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_worker_processes_job_for_seeded_conversation(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as session:
        ejob_repo = EmbeddingJobRepository(
            session, org_id=org_id, workspace_id=ws_id, user_id=user_id
        )
        await ejob_repo.enqueue(conversation_id=conv_id)
    worker = EmbeddingWorker(_FakeProvider())
    job = await worker._claim_one()
    assert job is not None
    async with async_session_maker() as session:
        chunk_repo = ConversationChunkRepository(
            session, org_id=org_id, workspace_id=ws_id, user_id=user_id
        )
        n = await chunk_repo.count_for_conversation(conv_id)
    assert n > 0


@pytest.mark.asyncio
async def test_worker_writes_null_embedding_when_provider_absent(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """Lexical-only mode: worker still chunks, but embedding column is NULL."""
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as session:
        ejob_repo = EmbeddingJobRepository(
            session, org_id=org_id, workspace_id=ws_id, user_id=user_id
        )
        await ejob_repo.enqueue(conversation_id=conv_id)
    worker = EmbeddingWorker(None)
    job = await worker._claim_one()
    assert job is not None
    async with async_session_maker() as session:
        chunk_repo = ConversationChunkRepository(
            session, org_id=org_id, workspace_id=ws_id, user_id=user_id
        )
        n = await chunk_repo.count_for_conversation(conv_id)
    assert n > 0
    async with async_session_maker() as session:
        result = await session.execute(
            sql_text(
                "SELECT COUNT(*) FROM conversation_chunks "
                "WHERE conversation_id = :cid AND embedding IS NULL "
                "AND embed_model = ''"
            ),
            {"cid": conv_id},
        )
        null_count = int(result.scalar_one())
    assert null_count == n


@pytest.mark.asyncio
async def test_reap_stuck_returns_running_to_pending(
    seeded_conversation: tuple[str, str, str, str],
) -> None:
    """A job stuck in 'running' beyond threshold is reset to 'pending'."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from cubebox.models.embedding_job import EmbeddingJob, EmbeddingJobState

    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as session:
        repo = EmbeddingJobRepository(session, org_id=org_id, workspace_id=ws_id, user_id=user_id)
        job = await repo.enqueue(conversation_id=conv_id)
    # Pretend the worker claimed it an hour ago and then crashed.
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    async with async_session_maker() as session:
        await session.execute(
            update(EmbeddingJob)
            .where(EmbeddingJob.id == job.id)
            .values(state=EmbeddingJobState.running, claimed_at=one_hour_ago)
        )
        await session.commit()
    async with async_session_maker() as session:
        reaped = await EmbeddingJobRepository(session).reap_stuck(threshold_seconds=1800)
    assert reaped == 1
    async with async_session_maker() as session:
        result = await session.execute(select(EmbeddingJob).where(EmbeddingJob.id == job.id))
        row = result.scalar_one()
    assert row.state == EmbeddingJobState.pending
    assert row.attempts == 1
    assert row.claimed_at is None
