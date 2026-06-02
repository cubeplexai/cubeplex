"""Per-replica poller that claims due scheduled tasks and dispatches runs.

Every replica runs one of these. Claiming is done with SELECT … FOR UPDATE SKIP
LOCKED so concurrent pollers never grab the same row. The occurrence-history row
(unique on (task_id, scheduled_for)) is the durable reservation; dispatch
happens AFTER commit so a long start_run never holds a Postgres row lock.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from loguru import logger
from sqlalchemy import case, literal, update
from sqlalchemy.exc import IntegrityError
from uuid_utils import uuid7

from cubebox.db.engine import async_session_maker
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.repositories.scheduled_task import (
    claim_busy_postponed_runs,
    claim_due_tasks,
    claim_stale_runs,
    fail_stale_started_runs,
)
from cubebox.schedules.compute import (
    MissedDecision,
    as_utc,
    decide_missed,
    latest_due_before,
    next_fire_after,
)
from cubebox.schedules.dispatch import (
    ConversationBusyError,
    ConversationPausedError,
    TargetUnavailableError,
    dispatch_scheduled_run,
)
from cubebox.streams.run_manager import RunManager


class ScheduledTaskPoller:
    def __init__(
        self,
        *,
        run_manager: RunManager,
        poll_interval_seconds: float = 15.0,
        jitter_seconds: float = 5.0,
        misfire_grace_seconds: int = 300,
        claim_timeout_seconds: int = 120,
        max_claims: int = 3,
        started_timeout_seconds: int = 3600,
        busy_retry_delay_seconds: int = 300,
        max_busy_retries: int = 3,
        batch_limit: int = 50,
    ) -> None:
        self._run_manager = run_manager
        self._poll_interval = poll_interval_seconds
        self._jitter = jitter_seconds
        self._grace = misfire_grace_seconds
        self._claim_timeout = timedelta(seconds=claim_timeout_seconds)
        self._started_timeout = timedelta(seconds=started_timeout_seconds)
        self._max_claims = max_claims
        self._busy_retry_delay = timedelta(seconds=busy_retry_delay_seconds)
        self._max_busy_retries = max_busy_retries
        self._batch_limit = batch_limit
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="scheduled-task-poller")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("scheduled-task poll failed", exc_info=True)
            await asyncio.sleep(self._poll_interval + random.uniform(0, self._jitter))

    async def poll_once(self) -> None:
        """One claim transaction + post-commit dispatch. Public for tests."""
        now = datetime.now(UTC)
        to_dispatch: list[str] = []

        async with async_session_maker() as session:
            due = await claim_due_tasks(session, now=now, limit=self._batch_limit)
            for task in due:
                row_id = self._claim_occurrence(session, task=task, now=now)
                if row_id is not None:
                    to_dispatch.append(row_id)

            stale = await claim_stale_runs(
                session,
                now=now,
                claim_timeout=self._claim_timeout,
                limit=self._batch_limit,
            )
            for row in stale:
                if row.claim_count >= self._max_claims:
                    row.state = "failed"
                    row.detail = "max re-claims exceeded"
                else:
                    row.claimed_at = now
                    row.claim_count += 1
                    # Drop any pre-stamped run_id from the prior dispatch
                    # attempt; the next _dispatch_one pre-stamps a fresh
                    # uuid, and the orphaned uuid's completion hook (if it
                    # ever fires from a dead replica's leftover Redis run)
                    # finds no matching row and becomes a no-op.
                    row.run_id = None
                    to_dispatch.append(row.id)

            busy = await claim_busy_postponed_runs(session, now=now, limit=self._batch_limit)
            for row in busy:
                row.claimed_at = now
                row.next_retry_at = None
                to_dispatch.append(row.id)

            await fail_stale_started_runs(
                session,
                now=now,
                started_timeout=self._started_timeout,
                limit=self._batch_limit,
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return

        for row_id in to_dispatch:
            await self._dispatch_one(row_id)

    def _claim_occurrence(self, session: Any, *, task: ScheduledTask, now: datetime) -> str | None:
        """Insert/handle the occurrence row + advance next_fire_at in the txn.

        Returns the new claimed-row id to dispatch after commit, or None
        (skipped / advanced only).
        """
        candidate = task.next_fire_at
        assert candidate is not None
        candidate = as_utc(candidate)
        if task.schedule_kind == "once":
            latest_due = candidate
        else:
            latest_due = latest_due_before(
                kind=task.schedule_kind,
                candidate=candidate,
                now=now,
                cron_expr=task.cron_expr,
                interval_seconds=task.interval_seconds,
                tz=task.timezone,
            )
        skipped_older = latest_due > candidate
        decision = decide_missed(latest_due=latest_due, now=now, grace_seconds=self._grace)

        if task.schedule_kind == "once":
            task.next_fire_at = None
        else:
            task.next_fire_at = next_fire_after(
                kind=task.schedule_kind,
                after=latest_due,
                cron_expr=task.cron_expr,
                interval_seconds=task.interval_seconds,
                tz=task.timezone,
            )

        if decision is MissedDecision.SKIP_MISSED:
            session.add(
                ScheduledTaskRun(
                    scheduled_task_id=task.id,
                    org_id=task.org_id,
                    workspace_id=task.workspace_id,
                    scheduled_for=latest_due,
                    claimed_at=now,
                    state="skipped_missed",
                    detail=(f"missed beyond grace; latest_due={latest_due.isoformat()}"),
                )
            )
            return None

        if skipped_older:
            session.add(
                ScheduledTaskRun(
                    scheduled_task_id=task.id,
                    org_id=task.org_id,
                    workspace_id=task.workspace_id,
                    scheduled_for=candidate,
                    claimed_at=now,
                    state="skipped_missed",
                    detail=(
                        f"caught up: skipped {candidate.isoformat()}..{latest_due.isoformat()}"
                    ),
                )
            )
        row = ScheduledTaskRun(
            scheduled_task_id=task.id,
            org_id=task.org_id,
            workspace_id=task.workspace_id,
            scheduled_for=latest_due,
            claimed_at=now,
            state="claimed",
        )
        session.add(row)
        task.last_fired_at = now
        return row.id

    async def _dispatch_one(self, row_id: str) -> None:
        async with async_session_maker() as session:
            row = await session.get(ScheduledTaskRun, row_id)
            if row is None or row.state != "claimed":
                return
            task = await session.get(ScheduledTask, row.scheduled_task_id)
            if task is None:
                row.state = "failed"
                row.detail = "task gone"
                await session.commit()
                return
            # Guard against stale-claim or busy-retry dispatching after end_at.
            # claim_due_tasks already filters, but recovery sweeps do not join
            # the parent task — this is the catch-all for those paths.
            if task.end_at is not None and as_utc(task.end_at) <= datetime.now(UTC):
                row.state = "skipped_missed"
                row.detail = "task expired before dispatch"
                await session.commit()
                return
            # Pre-stamp run_id while the row is still 'claimed' so the
            # completion hook can find the row by run_id even if the
            # background run finishes faster than the post-dispatch UPDATE
            # below. The hook's state filter accepts ('claimed', 'started').
            pre_run_id = str(uuid7())
            row.run_id = pre_run_id
            await session.commit()
            try:
                result = await dispatch_scheduled_run(
                    task=task, run_manager=self._run_manager, run_id=pre_run_id
                )
            except TargetUnavailableError as exc:
                # No run was started — clear the pre-stamped run_id so the
                # hook never matches a phantom row, and mark the occurrence
                # failed.
                row.run_id = None
                row.state = "failed"
                row.detail = str(exc)
                await session.commit()
                return
            except ConversationPausedError as exc:
                # The target conversation is waiting on a user HITL answer.
                # Busy-retry would burn the retry budget on a state only the
                # user can clear, so terminate this occurrence cleanly and
                # let the next scheduled fire decide on its own (the user
                # may have answered by then).
                row.run_id = None
                row.state = "skipped_paused"
                row.next_retry_at = None
                row.detail = f"target conversation paused on pending HITL: {exc}"
                await session.commit()
                return
            except ConversationBusyError as exc:
                # start_run rejected before launching any task; the
                # pre-stamped run_id was never used. Clear it so future
                # retries (which pre-stamp a new uuid) can't collide.
                row.run_id = None
                if row.retry_count + 1 >= self._max_busy_retries:
                    row.state = "skipped_busy_max_retries"
                    row.retry_count = row.retry_count + 1
                    row.next_retry_at = None
                    row.detail = f"target conversation busy after {row.retry_count} retries: {exc}"
                else:
                    row.retry_count = row.retry_count + 1
                    row.next_retry_at = datetime.now(UTC) + self._busy_retry_delay
                    row.detail = (
                        f"target conversation busy; retry {row.retry_count}"
                        f"/{self._max_busy_retries} at "
                        f"{row.next_retry_at.isoformat()}"
                    )
                await session.commit()
                return
            # Backfill conversation_id + started_at unconditionally so the
            # run link survives the race where the completion hook flipped
            # state to terminal before this UPDATE landed (without it the
            # UI shows the run as succeeded/failed but has no "View
            # conversation" link because conversation_id stayed NULL).
            # Only flip state → 'started' when it is still 'claimed' — a
            # CASE expression keeps the terminal state intact otherwise.
            await session.execute(
                update(ScheduledTaskRun)
                .where(ScheduledTaskRun.id == row.id)  # type: ignore[arg-type]
                .values(
                    conversation_id=result.conversation_id,
                    started_at=datetime.now(UTC),
                    state=case(
                        (cast(Any, ScheduledTaskRun.state) == "claimed", literal("started")),
                        else_=ScheduledTaskRun.state,
                    ),
                )
            )
            await session.commit()
