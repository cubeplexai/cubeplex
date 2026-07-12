"""Repository for the async embedding queue.

Mixes scoped (`enqueue`) and process-global (`claim_batch`, `mark_done`,
`mark_failed`, `reap_stuck`) operations on the same model. The worker is a
single background daemon — it has no request context — so the consume-side
methods deliberately do not filter by (org_id, workspace_id, user_id) and
operate on already-claimed rows by id. `enqueue` is the only request-side
entry point and uses scope from the constructor.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.embedding_job import EmbeddingJob, EmbeddingJobState
from cubeplex.repositories.base import ScopedRepository


class EmbeddingJobRepository(ScopedRepository[EmbeddingJob]):
    model = EmbeddingJob

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str = "",
        workspace_id: str = "",
        user_id: str = "",
    ) -> None:
        # org/workspace/user default to empty strings so the worker (which
        # only calls claim_batch / mark_done / mark_failed / reap_stuck) can
        # construct without supplying scope. enqueue raises via the FK /
        # NOT NULL constraints if scope is missing.
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)
        self.user_id = user_id

    async def enqueue(
        self,
        *,
        conversation_id: str,
        seq_lo: int = 0,
        seq_hi: int = 2**62,
    ) -> EmbeddingJob:
        """Enqueue an indexing job, merging with an existing pending/running job
        for the same conversation when one already exists.

        Merging prevents duplicate jobs from racing to DELETE-then-INSERT chunks
        and violating the ``ix_chunks_conversation`` unique constraint.
        """
        # Check for an existing pending/running job for this conversation. If
        # one exists, bump its seq_hi to cover the union of both windows and
        # skip inserting a duplicate.  FOR UPDATE serialises concurrent callers
        # so exactly one row survives.
        stmt = (
            select(EmbeddingJob)
            .where(
                EmbeddingJob.conversation_id == conversation_id,  # type: ignore[arg-type]
                EmbeddingJob.org_id == self.org_id,  # type: ignore[arg-type]
                EmbeddingJob.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                EmbeddingJob.creator_user_id == self.user_id,  # type: ignore[arg-type]
                EmbeddingJob.state.in_(  # type: ignore[attr-defined]
                    [EmbeddingJobState.pending, EmbeddingJobState.running]
                ),
            )
            .with_for_update()
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.seq_hi = max(existing.seq_hi, seq_hi)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        job = EmbeddingJob(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            creator_user_id=self.user_id,
            conversation_id=conversation_id,
            seq_lo=seq_lo,
            seq_hi=seq_hi,
            state=EmbeddingJobState.pending,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def claim_batch(self, limit: int) -> list[EmbeddingJob]:
        """Claim up to `limit` pending jobs whose scheduled_at <= now().

        Process-global: the worker runs as a single background daemon, not
        per-request, so no scope filter is applied.
        """
        sql = text(
            """
            UPDATE embedding_jobs
            SET state = 'running', claimed_at = now(), updated_at = now()
            WHERE id IN (
                SELECT id FROM embedding_jobs
                WHERE state = 'pending' AND scheduled_at <= now()
                ORDER BY scheduled_at
                FOR UPDATE SKIP LOCKED
                LIMIT :lim
            )
            RETURNING id
            """
        )
        result = await self.session.execute(sql, {"lim": limit})
        ids = [row[0] for row in result.fetchall()]
        await self.session.commit()
        if not ids:
            return []
        stmt = select(EmbeddingJob).where(
            EmbeddingJob.id.in_(ids)  # type: ignore[attr-defined]
        )
        result2 = await self.session.execute(stmt)
        return list(result2.scalars().all())

    async def reap_stuck(self, *, threshold_seconds: int) -> int:
        """Return jobs stuck in 'running' beyond threshold to 'pending'.

        Process-global: see claim_batch. Bumps attempts so a permanently
        broken job (e.g. one that crashes the worker on every claim) still
        drops to 'dead' via mark_failed.
        """
        sql = text(
            """
            UPDATE embedding_jobs
            SET state = 'pending',
                attempts = attempts + 1,
                claimed_at = NULL,
                scheduled_at = now(),
                updated_at = now()
            WHERE state = 'running'
              AND claimed_at IS NOT NULL
              AND claimed_at < now() - make_interval(secs => :threshold)
            RETURNING id
            """
        )
        result = await self.session.execute(sql, {"threshold": int(threshold_seconds)})
        ids = [row[0] for row in result.fetchall()]
        await self.session.commit()
        return len(ids)

    async def mark_done(self, job_id: str) -> None:
        await self.session.execute(
            text("UPDATE embedding_jobs SET state='done', updated_at=now() WHERE id=:id"),
            {"id": job_id},
        )
        await self.session.commit()

    async def mark_failed(
        self,
        job_id: str,
        error: str,
        prior_attempts: int,
        backoff_seconds: list[int],
        max_attempts: int,
    ) -> None:
        """Record a failure.

        `prior_attempts` is the row's current `attempts` value (the count
        before this failure). The new written value is `prior_attempts + 1`.
        The backoff index reads `backoff_seconds[prior_attempts]`, so a
        first failure (prior=0) waits `backoff_seconds[0]` and the
        configured tail entry actually gets used.
        """
        new_attempts = prior_attempts + 1
        if new_attempts >= max_attempts:
            await self.session.execute(
                text(
                    "UPDATE embedding_jobs SET state='dead', "
                    "attempts=:a, last_error=:err, updated_at=now() WHERE id=:id"
                ),
                {"id": job_id, "a": new_attempts, "err": error[:2000]},
            )
        else:
            delay = backoff_seconds[min(prior_attempts, len(backoff_seconds) - 1)]
            next_at = datetime.now(UTC) + timedelta(seconds=delay)
            await self.session.execute(
                text(
                    "UPDATE embedding_jobs SET state='pending', "
                    "attempts=:a, last_error=:err, scheduled_at=:s, updated_at=now() "
                    "WHERE id=:id"
                ),
                {"id": job_id, "a": new_attempts, "err": error[:2000], "s": next_at},
            )
        await self.session.commit()
