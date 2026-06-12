"""Drains embedding_jobs: claim → load messages → chunk → embed → write."""

import asyncio
import logging
from collections.abc import Sequence

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.config import config
from cubebox.db.engine import async_session_maker
from cubebox.models.conversation_chunk import ConversationChunk
from cubebox.models.embedding_job import EmbeddingJob
from cubebox.repositories.conversation_chunk import ConversationChunkRepository
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.search.chunker import MessageInput, chunk_messages
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.text_extract import extract_searchable_text

logger = logging.getLogger(__name__)


class EmbeddingWorker:
    def __init__(self, provider: EmbeddingProvider | None) -> None:
        # provider=None is the lexical-only degraded mode: the worker still
        # chunks and writes rows so PGroonga has something to query, but
        # leaves embedding=NULL. Backfill re-embeds those rows once an
        # operator configures a provider.
        self._provider = provider
        self._stop = asyncio.Event()
        self._poll_interval = int(config.get("search.worker.poll_interval_seconds", 2))
        self._max_attempts = int(config.get("search.worker.max_attempts", 5))
        self._backoff: Sequence[int] = list(
            config.get("search.worker.backoff_seconds", [60, 300, 1500, 7200, 36000])
        )
        self._target_tokens = int(config.get("search.chunker.target_tokens", 600))
        self._overlap_tokens = int(config.get("search.chunker.overlap_tokens", 100))
        self._stuck_threshold = int(config.get("search.worker.stuck_threshold_seconds", 1800))

    async def run(self) -> None:
        logger.info("EmbeddingWorker started")
        # Reap jobs left in 'running' by a crashed prior worker before draining
        # the normal queue. Idempotent — a healthy queue will reap zero rows.
        try:
            async with async_session_maker() as session:
                reaped = await EmbeddingJobRepository(session).reap_stuck(
                    threshold_seconds=self._stuck_threshold
                )
            if reaped:
                logger.warning("Reaped %d stuck embedding job(s) on startup", reaped)
        except Exception:
            logger.exception("EmbeddingWorker reap_stuck failed; continuing")
        while not self._stop.is_set():
            try:
                claimed = await self._claim_one()
                if claimed is None:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
            except Exception:
                logger.exception("EmbeddingWorker loop error")
                await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._stop.set()

    async def _claim_one(self) -> EmbeddingJob | None:
        async with async_session_maker() as session:
            jobs = await EmbeddingJobRepository(session).claim_batch(limit=1)
        if not jobs:
            return None
        job = jobs[0]
        try:
            await self._process(job)
            async with async_session_maker() as session:
                await EmbeddingJobRepository(session).mark_done(job.id)
            return job
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            async with async_session_maker() as session:
                await EmbeddingJobRepository(session).mark_failed(
                    job_id=job.id,
                    error=str(exc),
                    prior_attempts=job.attempts,
                    backoff_seconds=list(self._backoff),
                    max_attempts=self._max_attempts,
                )
            return job

    async def _process(self, job: EmbeddingJob) -> None:
        # 1. Load all messages for the conversation (cubepi load is per-thread).
        async with init_checkpointer() as cp:
            data = await cp.load(job.conversation_id)
        if data is None:
            return
        # 2. Filter to (seq_lo, seq_hi) window.
        #
        # We use 1-based load-order as the seq. This matches what the
        # frontend conversation page uses for its `#msg-N` anchors —
        # both sides walk cubepi's `data.messages` in order. The seq
        # is a navigation hint, not an authoritative cubepi reference.
        # If cubepi ever starts filtering tombstones / system messages
        # from `data.messages`, both sides shift together, anchors stay
        # consistent.
        in_window = [
            (idx + 1, m)
            for idx, m in enumerate(data.messages)
            if job.seq_lo <= idx + 1 <= job.seq_hi
        ]
        # 3. Extract searchable text per message.
        inputs: list[MessageInput] = []
        for seq, m in in_window:
            text = extract_searchable_text(m)
            if text:
                inputs.append(MessageInput(seq=seq, text=text))
        # 4. Chunk.
        chunks = chunk_messages(
            inputs, target_tokens=self._target_tokens, overlap_tokens=self._overlap_tokens
        )
        if not chunks:
            return
        # 5. Embed (skipped in lexical-only mode).
        if self._provider is None:
            # Sentinel embed_model so backfill can `WHERE embed_model = ''`
            # to find rows that still need vectors after a key is configured.
            rows = [
                ConversationChunk(
                    chunk_seq=c.chunk_seq,
                    seq_lo=c.seq_lo,
                    seq_hi=c.seq_hi,
                    text=c.text,
                    embedding=None,
                    embed_model="",
                )
                for c in chunks
            ]
        else:
            vectors = await self._provider.embed([c.text for c in chunks])
            # zip(..., strict=True) raises a ValueError whose message ('zip()
            # argument 2 is shorter/longer than argument 1') is opaque in logs.
            # An explicit check gives operators a greppable root cause and the
            # same retry path via _claim_one's except.
            if len(vectors) != len(chunks):
                raise RuntimeError(
                    f"embedding provider returned {len(vectors)} vectors for {len(chunks)} inputs"
                )
            rows = [
                ConversationChunk(
                    chunk_seq=c.chunk_seq,
                    seq_lo=c.seq_lo,
                    seq_hi=c.seq_hi,
                    text=c.text,
                    embedding=v,
                    embed_model=self._provider.model_id,
                )
                for c, v in zip(chunks, vectors, strict=False)
            ]
        # 6. Persist.
        async with async_session_maker() as session:
            repo = ConversationChunkRepository(
                session,
                org_id=job.org_id,
                workspace_id=job.workspace_id,
                user_id=job.creator_user_id,
            )
            await repo.replace_for_conversation(
                conversation_id=job.conversation_id,
                chunks=rows,
            )
