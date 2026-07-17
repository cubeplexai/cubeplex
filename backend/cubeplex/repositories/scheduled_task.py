"""Repositories for scheduled tasks and their occurrence history.

``ScheduledTaskRunRepository.claim_due_tasks`` is the poller's hot path. It uses
``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent pollers on different replicas
never claim the same row; the caller advances ``next_fire_at`` and inserts the
history row in the SAME transaction so the claim is atomic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.repositories.base import ScopedRepository


class ScheduledTaskRepository(ScopedRepository[ScheduledTask]):
    model = ScheduledTask

    def _scoped_select(self) -> Any:
        return super()._scoped_select().where(cast(Any, ScheduledTask.deleted_at).is_(None))

    async def create(self, task: ScheduledTask) -> ScheduledTask:
        return await self.add(task)

    async def get_active(self, task_id: str) -> ScheduledTask | None:
        return await self.get(task_id)

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[ScheduledTask]:
        stmt = (
            self._scoped_select()
            .order_by(ScheduledTask.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, task_id: str) -> bool:
        task = await self.get(task_id)
        if task is None:
            return False
        task.deleted_at = datetime.now(UTC)
        task.next_fire_at = None
        await self.session.commit()
        return True


class ScheduledTaskRunRepository(ScopedRepository[ScheduledTaskRun]):
    model = ScheduledTaskRun

    async def list_for_task(
        self, task_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[ScheduledTaskRun]:
        stmt = (
            self._scoped_select()
            .where(ScheduledTaskRun.scheduled_task_id == task_id)
            .order_by(ScheduledTaskRun.scheduled_for.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


async def claim_due_tasks(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[ScheduledTask]:
    """Lock and return active, non-deleted, due tasks. Caller is inside a txn.

    NOT scoped to a single workspace: the poller runs cross-workspace per replica.
    ``FOR UPDATE SKIP LOCKED`` makes concurrent pollers safe.
    """
    stmt = (
        select(ScheduledTask)
        .where(
            ScheduledTask.status == "active",  # type: ignore[arg-type]
            cast(Any, ScheduledTask.deleted_at).is_(None),
            cast(Any, ScheduledTask.next_fire_at).is_not(None),
            ScheduledTask.next_fire_at <= now,  # type: ignore[arg-type, operator]
            or_(
                cast(Any, ScheduledTask.end_at).is_(None),
                ScheduledTask.end_at > now,  # type: ignore[operator, arg-type]
            ),
        )
        .order_by(ScheduledTask.next_fire_at)  # type: ignore[arg-type]
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def claim_stale_runs(
    session: AsyncSession, *, now: datetime, claim_timeout: timedelta, limit: int
) -> list[ScheduledTaskRun]:
    """Lock and return occurrence rows stuck in 'claimed' past claim_timeout.

    A row is stale when state='claimed' and ``claimed_at`` is older than
    ``claim_timeout``. Two crash points produce stale rows:

    * **run_id IS NULL** — a replica reserved the occurrence but died before
      ``_dispatch_one`` reached the pre-stamp step.
    * **run_id IS NOT NULL** — ``_dispatch_one`` pre-stamped the row's
      ``run_id`` (so the completion hook could find it during the dispatch
      race) but the replica died before either ``start_run`` succeeded or
      the post-dispatch UPDATE flipped state to 'started'. The pre-stamped
      uuid was either never used by ``start_run`` or used by a now-dead
      background task; the next replica must re-pick this row.

    The poller nulls out ``run_id`` when re-claiming so the next dispatch
    pre-stamps a fresh uuid and the orphaned uuid's completion hook (if it
    ever fires) becomes a no-op (no matching row).

    Excludes rows with a future ``next_retry_at`` — those are busy-target
    postpones owned by ``claim_busy_postponed_runs`` and must not be picked
    up early by the stale sweep (claim_timeout=120s default is shorter than
    busy_retry_delay=300s default, so without this exclusion the busy retry
    cadence collapses into the stale-claim cadence).
    """
    cutoff = now - claim_timeout
    stmt = (
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "claimed",  # type: ignore[arg-type]
            cast(Any, ScheduledTaskRun.next_retry_at).is_(None),
            ScheduledTaskRun.claimed_at < cutoff,  # type: ignore[arg-type]
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def claim_busy_postponed_runs(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[ScheduledTaskRun]:
    """Lock and return occurrence rows postponed because the fixed target was busy.

    No retry-count cap in the query: the cap is enforced in the poller's
    ``_dispatch_one`` (rows past ``max_busy_retries`` are flipped to terminal
    ``skipped_busy_max_retries``, so the ``state='claimed'`` filter already
    excludes them). Hard-coding a literal cap here would strand rows when
    ``max_busy_retries`` is configured higher than the literal.
    """
    stmt = (
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "claimed",  # type: ignore[arg-type]
            cast(Any, ScheduledTaskRun.run_id).is_(None),
            cast(Any, ScheduledTaskRun.next_retry_at).is_not(None),
            ScheduledTaskRun.next_retry_at <= now,  # type: ignore[arg-type, operator]
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def fail_stale_started_runs(
    session: AsyncSession, *, now: datetime, started_timeout: timedelta, limit: int
) -> int:
    """Fail occurrence rows stuck in 'started' long past any plausible run.

    A replica can die AFTER start_run (state='started', run_id set) but BEFORE
    the run-completion hook fires, leaving the row 'started' forever. There is
    no live run to recover, so after a generous timeout mark it 'failed' so
    history is not stuck. Returns the number of rows failed.
    """
    cutoff = now - started_timeout
    stmt = (
        update(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "started",  # type: ignore[arg-type]
            ScheduledTaskRun.started_at < cutoff,  # type: ignore[arg-type, operator]
        )
        .values(
            state="failed",
            detail="run did not report terminal status in time",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    cursor = cast(CursorResult[tuple[()]], await session.execute(stmt))
    return int(cursor.rowcount or 0)
