# Workspace Scheduled Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a workspace member define a task that fires agent runs on a schedule (cron / interval / one-shot), executed as the owning user through the existing `RunManager.start_run` path, reliably across N replicas with no double-fire and an explicit missed-run policy.

**Architecture:** Two Postgres tables (`scheduled_tasks` + `scheduled_task_runs`) plus a per-replica `ScheduledTaskPoller` started in the FastAPI lifespan. Every replica polls due rows with `SELECT … FOR UPDATE SKIP LOCKED`, inserts an occurrence-history row keyed `(task_id, scheduled_for)` (the unique constraint is the idempotency key), advances `next_fire_at` in the same transaction, then — after commit — dispatches via the unchanged `run_manager.start_run`. The occurrence moves `claimed → started`; the `succeeded`/`failed` terminal state is written by a run-completion hook keyed on `run_id`. A reaper re-claims stale `claimed` rows (at-least-once) and caps retries via `claim_count`.

**Tech Stack:** FastAPI, SQLModel + Alembic, asyncpg/Postgres (`FOR UPDATE SKIP LOCKED`), Redis (run metadata, via existing `RunManager`), `croniter` for cron-next computation, pytest E2E against the per-slot worktree DB.

---

> **Worktree first.** This plan executes inside
> `/home/chris/cubeplex/.worktrees/feat/workspace-scheduled-tasks` on branch
> `feat/workspace-scheduled-tasks`. First commands in any fresh session:
> ```bash
> cat .worktree.env            # ports 8091 / 3091, DB cubeplex_feat_workspace_scheduled_tasks
> ./scripts/worktree-env doctor
> ```
> All `uv run` / `alembic` / `pytest` commands below run from `backend/`. The
> conftest auto-routes worktree tests to `cubeplex_test_<slug>` — plain
> `uv run pytest` never touches the dev DB.

---

## File Structure

**New files:**
- `backend/cubeplex/models/scheduled_task.py` — `ScheduledTask` + `ScheduledTaskRun` SQLModel tables.
- `backend/cubeplex/schedules/__init__.py` — package marker.
- `backend/cubeplex/schedules/compute.py` — pure functions: `next_fire_after`, `latest_due_before`, `decide_missed`.
- `backend/cubeplex/repositories/scheduled_task.py` — `ScheduledTaskRepository` + `ScheduledTaskRunRepository` (claim SQL).
- `backend/cubeplex/schedules/dispatch.py` — `resolve_target` + `dispatch_scheduled_run` (the shared seam).
- `backend/cubeplex/schedules/poller.py` — `ScheduledTaskPoller` loop.
- `backend/cubeplex/schedules/completion_hook.py` — `record_scheduled_run_terminal_state`.
- `backend/cubeplex/api/schemas/ws_scheduled_tasks.py` — request/response Pydantic models.
- `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py` — workspace router.
- `backend/tests/unit/test_schedule_compute.py` — pure-function unit tests.
- `backend/tests/e2e/test_scheduled_tasks_api.py` — CRUD + auth E2E.
- `backend/tests/e2e/test_scheduled_tasks_firing.py` — firing / missed-run / concurrency E2E.
- Frontend (scope-isolated page, spec §"Scope-isolated workspace API and page"):
  - `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/page.tsx` — the
    page (own Next route + page file; assembles list/detail/form modules — no
    `mode?` prop reuse of another page).
  - `frontend/packages/web/app/api/v1/ws/[wsId]/scheduled-tasks/route.ts`
    (and `[id]/route.ts`, `[id]/pause`, `[id]/resume`, `[id]/runs`) — the SSE-
    safe proxy handlers mirroring the existing `conversations` proxy layout.
  - `frontend/packages/core/src/types/scheduled-task.ts` — shared types.
  - `frontend/packages/core/src/hooks/useScheduledTasks.ts` (+ api client in
    `packages/core/src/api/`) — data hooks.
  (Match the exact file conventions of the existing `w/[wsId]/conversations`
  feature; build `@cubeplex/core` before the web app sees new types.)

**Modified files:**
- `backend/cubeplex/models/__init__.py` — export the two new models.
- `backend/cubeplex/api/app.py` — start/stop the poller in lifespan; include the router.
- `backend/cubeplex/streams/run_manager.py` — call the completion hook at terminal status.
- `backend/cubeplex/api/routes/v1/__init__.py` — re-export the router.
- `backend/pyproject.toml` / `backend/uv.lock` — add `croniter` (via `uv add`).
- Frontend nav (sidebar / workspace nav) — add a "Scheduled tasks" entry
  pointing at the new route, mirroring how the other `w/[wsId]` features
  register their nav item.

---

## Conventions locked in (read once)

- **Models:** `class ScheduledTask(CubeplexBase, OrgScopedMixin, table=True)` with
  `_PREFIX: ClassVar[str] = "stask"` and `_PREFIX = "stkrn"` for runs. PK auto-fills
  via `model_post_init`. Mirror `Conversation`'s soft-delete + partial deleted_at index.
- **Datetimes:** all DB datetimes surfaced through the API use `utc_isoformat()`
  (`from cubeplex.utils.time import utc_isoformat`). Stored times are UTC-naive-aware
  `datetime` with `tzinfo=UTC`.
- **Scoping:** repositories subclass `ScopedRepository[T]` so `(org_id, workspace_id)`
  is structural. `ConversationRepository(session, org_id, workspace_id, user_id=owner)`
  is how target ownership is enforced (it filters `creator_user_id`).
- **Auth:** `RequestContext` carries `.user`, `.org_id`, `.workspace_id`, `.role`
  (`Role.ADMIN`/`Role.MEMBER`). Reads use `Depends(require_member)`; mutations check
  owner-or-admin in the handler.
- **Run start:** `RunContext(user_id, org_id, workspace_id)` then
  `await run_manager.start_run(conversation_id=…, content=…, attachments=[], ctx=…)`
  returns a `run_id`. `start_run` raises `RuntimeError` if the conversation already
  has a running run.
- **Terminal status:** `_execute_run` calls `update_run_meta(..., status="completed"|
  "failed"|"cancelled")`. The completion hook is invoked at those three points.

---

### Task 1: Add the croniter dependency

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`

- [ ] **Step 1: Add the dependency (do not hand-edit pyproject)**

Run (from `backend/`):
```bash
uv add croniter
```
Expected: `uv` resolves and writes `croniter` to `[project.dependencies]` in
`pyproject.toml` and updates `uv.lock`; output ends with `+ croniter==...`.

- [ ] **Step 2: Verify import resolves**

Run:
```bash
uv run python -c "from croniter import croniter; print(croniter.is_valid('0 9 * * 1-5'))"
```
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "build(deps): add croniter for scheduled-task cron evaluation"
```

---

### Task 2: Pure schedule-compute functions (unit-tested)

These are the only pieces worth isolating as units (DST/grace logic). Everything
else is covered E2E.

**Files:**
- Create: `backend/cubeplex/schedules/__init__.py`
- Create: `backend/cubeplex/schedules/compute.py`
- Test: `backend/tests/unit/test_schedule_compute.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_schedule_compute.py
from datetime import UTC, datetime, timedelta

import pytest

from cubeplex.schedules.compute import (
    MissedDecision,
    decide_missed,
    latest_due_before,
    next_fire_after,
)

pytestmark = pytest.mark.unit


def _dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_interval_next_fire() -> None:
    anchor = _dt(2026, 1, 1, 9, 0)
    assert next_fire_after(
        kind="interval", interval_seconds=3600, after=anchor, tz="UTC"
    ) == anchor + timedelta(seconds=3600)


def test_cron_next_fire_weekday_9am_utc() -> None:
    # Friday 2026-01-02 09:00 → next weekday match is Monday 2026-01-05 09:00.
    after = _dt(2026, 1, 2, 9, 0)
    nxt = next_fire_after(kind="cron", cron_expr="0 9 * * 1-5", after=after, tz="UTC")
    assert nxt == _dt(2026, 1, 5, 9, 0)


def test_cron_evaluated_in_task_timezone() -> None:
    # 09:00 in America/New_York (UTC-5 in January) == 14:00 UTC.
    after = _dt(2026, 1, 1, 0, 0)
    nxt = next_fire_after(
        kind="cron", cron_expr="0 9 * * *", after=after, tz="America/New_York"
    )
    assert nxt == _dt(2026, 1, 1, 14, 0)


def test_latest_due_before_hourly_picks_most_recent_not_first() -> None:
    # Hourly task, poller wakes at 10:02; latest due is 10:00 not 08:00.
    last_next = _dt(2026, 1, 1, 8, 0)
    now = _dt(2026, 1, 1, 10, 2)
    latest = latest_due_before(
        kind="interval", interval_seconds=3600, candidate=last_next, now=now, tz="UTC"
    )
    assert latest == _dt(2026, 1, 1, 10, 0)


def test_decide_missed_within_grace_fires_latest() -> None:
    now = _dt(2026, 1, 1, 10, 2)
    latest = _dt(2026, 1, 1, 10, 0)
    d = decide_missed(latest_due=latest, now=now, grace_seconds=300)
    assert d == MissedDecision.FIRE


def test_decide_missed_past_grace_skips() -> None:
    now = _dt(2026, 1, 1, 10, 10)
    latest = _dt(2026, 1, 1, 10, 0)
    d = decide_missed(latest_due=latest, now=now, grace_seconds=300)
    assert d == MissedDecision.SKIP_MISSED


def test_compute_accepts_naive_db_datetimes() -> None:
    # Regression: DB columns are `timestamp without time zone`, so next_fire_at
    # reads back NAIVE. Mixing it with datetime.now(UTC) (aware) must NOT raise.
    naive_candidate = datetime(2026, 1, 1, 8, 0)  # no tzinfo, as from the DB
    aware_now = _dt(2026, 1, 1, 10, 2)
    latest = latest_due_before(
        kind="interval", interval_seconds=3600,
        candidate=naive_candidate, now=aware_now, tz="UTC",
    )
    assert latest == _dt(2026, 1, 1, 10, 0)
    # decide_missed and next_fire_after must also tolerate a naive input.
    assert decide_missed(latest_due=latest, now=aware_now, grace_seconds=300) == (
        MissedDecision.FIRE
    )
    assert next_fire_after(
        kind="interval", interval_seconds=3600, after=datetime(2026, 1, 1, 9, 0), tz="UTC"
    ) == _dt(2026, 1, 1, 10, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_schedule_compute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.schedules'`.

- [ ] **Step 3: Implement the pure functions**

```python
# backend/cubeplex/schedules/__init__.py
```
(empty file)

```python
# backend/cubeplex/schedules/compute.py
"""Pure schedule arithmetic: next-fire, latest-due catch-up, missed-run decision.

Cron is evaluated in the task's IANA timezone; all returned datetimes are UTC.
No DB, no I/O — these are the only unit-tested pieces of the feature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from croniter import croniter


class MissedDecision(StrEnum):
    FIRE = "fire"
    SKIP_MISSED = "skip_missed"


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime; pass through aware ones.

    cubeplex stores `timestamp without time zone`, so datetimes read back from
    the DB are NAIVE. The compute/poller arithmetic mixes those with
    `datetime.now(UTC)` (AWARE); comparing or subtracting the two raises
    TypeError. Every datetime that came from the DB MUST pass through this
    before any comparison/subtraction here.
    """
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def next_fire_after(
    *,
    kind: str,
    after: datetime,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    tz: str = "UTC",
) -> datetime:
    """Return the first occurrence strictly after ``after`` (UTC)."""
    after = as_utc(after)  # DB datetimes are naive; normalize before arithmetic
    if kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60")
        return after + timedelta(seconds=interval_seconds)
    if kind == "cron":
        if cron_expr is None:
            raise ValueError("cron_expr required for cron schedule")
        zone = ZoneInfo(tz)
        base = after.astimezone(zone)
        nxt = croniter(cron_expr, base).get_next(datetime)
        return nxt.astimezone(UTC)
    raise ValueError(f"next_fire_after not defined for kind={kind!r}")


def latest_due_before(
    *,
    kind: str,
    candidate: datetime,
    now: datetime,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    tz: str = "UTC",
) -> datetime:
    """The latest scheduled occurrence <= now, starting from ``candidate``.

    ``candidate`` is the task's stored ``next_fire_at`` (the first due occurrence).
    Walk forward in arithmetic to the last slot that is still <= now. Avoids the
    bug of treating the first stale occurrence as the grace test.
    """
    candidate = as_utc(candidate)  # candidate is task.next_fire_at (naive from DB)
    now = as_utc(now)
    if candidate > now:
        return candidate
    if kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60")
        elapsed = int((now - candidate).total_seconds())
        steps = elapsed // interval_seconds
        return candidate + timedelta(seconds=steps * interval_seconds)
    if kind == "cron":
        if cron_expr is None:
            raise ValueError("cron_expr required for cron schedule")
        zone = ZoneInfo(tz)
        itr = croniter(cron_expr, candidate.astimezone(zone))
        latest = candidate
        # candidate is itself a valid cron match; advance while next <= now.
        while True:
            nxt = itr.get_next(datetime).astimezone(UTC)
            if nxt > now:
                break
            latest = nxt
        return latest
    raise ValueError(f"latest_due_before not defined for kind={kind!r}")


def decide_missed(
    *, latest_due: datetime, now: datetime, grace_seconds: int
) -> MissedDecision:
    """Fire the latest due occurrence if within grace, else skip it as missed."""
    now, latest_due = as_utc(now), as_utc(latest_due)  # DB datetimes may be naive
    if (now - latest_due).total_seconds() <= grace_seconds:
        return MissedDecision.FIRE
    return MissedDecision.SKIP_MISSED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_schedule_compute.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/schedules/__init__.py backend/cubeplex/schedules/compute.py backend/tests/unit/test_schedule_compute.py
git commit -m "feat(schedules): pure cron/interval next-fire + missed-run compute"
```

---

### Task 3: The two SQLModel tables + public_id prefixes

**Files:**
- Create: `backend/cubeplex/models/scheduled_task.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: Write the model module**

```python
# backend/cubeplex/models/scheduled_task.py
"""Scheduled-task tables.

``ScheduledTask`` is the schedule definition; ``ScheduledTaskRun`` is the
per-occurrence history row. The unique ``(scheduled_task_id, scheduled_for)``
constraint on the history table is the occurrence-idempotency key: inserting it
is the act that claims an occurrence, so two racing pollers produce one row.
Soft delete mirrors ``Conversation`` (stamp ``deleted_at``; the poller filters
``deleted_at IS NULL``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, Index, Integer, UniqueConstraint, text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin


class ScheduledTask(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "stask"
    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        Index("ix_scheduled_tasks_org_ws", "org_id", "workspace_id"),
        # Poller hot query: WHERE status='active' AND next_fire_at <= now().
        Index("ix_scheduled_tasks_status_next_fire", "status", "next_fire_at"),
        Index(
            "ix_scheduled_tasks_deleted_at_partial",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )

    owner_user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    name: str = Field(max_length=255)
    status: str = Field(default="active", max_length=16)  # active | paused

    schedule_kind: str = Field(max_length=16)  # cron | interval | once
    cron_expr: str | None = Field(default=None, max_length=255)
    interval_seconds: int | None = Field(default=None)
    run_at: datetime | None = Field(default=None)
    timezone: str = Field(default="UTC", max_length=64)

    prompt: str = Field()
    agent_config_id: str | None = Field(
        default=None, foreign_key="agent_configs.id", max_length=20
    )

    target_mode: str = Field(max_length=16)  # fixed | new_each_run
    target_conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", max_length=20
    )

    next_fire_at: datetime | None = Field(default=None, index=True)
    last_fired_at: datetime | None = Field(default=None)
    deleted_at: datetime | None = Field(default=None)


class ScheduledTaskRun(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "stkrn"
    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        Index("ix_scheduled_task_runs_org_ws", "org_id", "workspace_id"),
        UniqueConstraint(
            "scheduled_task_id", "scheduled_for", name="uq_stkrn_task_scheduled_for"
        ),
        # Reaper query: claimed rows with run_id NULL older than claim_timeout.
        Index("ix_stkrn_state_claimed_at", "state", "claimed_at"),
        # Hook lookup by run_id.
        Index("ix_stkrn_run_id", "run_id"),
    )

    scheduled_task_id: str = Field(
        foreign_key="scheduled_tasks.id", max_length=20, index=True
    )
    scheduled_for: datetime = Field()
    claimed_at: datetime = Field()
    started_at: datetime | None = Field(default=None)
    # claimed | started | succeeded | failed | skipped_missed |
    # skipped_busy_max_retries
    state: str = Field(max_length=32)
    claim_count: int = Field(default=1)
    # Busy-conversation retry path (spec §"One-run-per-conversation interaction"):
    # on a busy fixed target, set next_retry_at = now + 5m, bump retry_count, leave
    # the row re-claimable; after retry_count >= 3, mark skipped_busy_max_retries.
    retry_count: int = Field(
        default=0,
        sa_column=Column(Integer(), nullable=False, server_default="0"),
    )
    next_retry_at: datetime | None = Field(default=None, nullable=True)
    run_id: str | None = Field(default=None, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=20)
    detail: str | None = Field(default=None)

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
```

- [ ] **Step 2: Export the models**

In `backend/cubeplex/models/__init__.py`, add an import next to the
`Conversation` import and the names to `__all__`:
```python
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
```
and inside `__all__` add `"ScheduledTask",` and `"ScheduledTaskRun",`.

- [ ] **Step 3: Verify the models import and prefixes validate**

Run (from `backend/`):
```bash
uv run python -c "from cubeplex.models import ScheduledTask, ScheduledTaskRun; t=ScheduledTask(name='x', schedule_kind='once', prompt='p', target_mode='new_each_run', org_id='o', workspace_id='w', owner_user_id='u'); print(t.id[:6])"
```
Expected: prints `stask-` (the auto-filled prefixed id).

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/models/scheduled_task.py backend/cubeplex/models/__init__.py
git commit -m "feat(models): scheduled_tasks + scheduled_task_runs tables"
```

---

### Task 4: Alembic autogenerate migration

**Files:**
- Create: `backend/alembic/versions/<rev>_scheduled_tasks.py` (generated)

- [ ] **Step 1: Generate the migration (do NOT hand-write)**

Run (from `backend/`):
```bash
uv run alembic revision --autogenerate -m "scheduled_tasks"
```
Expected: a new file under `backend/alembic/versions/` whose `upgrade()` creates
`scheduled_tasks` and `scheduled_task_runs`, the `uq_stkrn_task_scheduled_for`
unique constraint, the `ix_scheduled_tasks_status_next_fire` /
`ix_stkrn_state_claimed_at` / `ix_stkrn_run_id` indexes, and the partial
`ix_scheduled_tasks_deleted_at_partial` index. The `scheduled_task_runs` create
must also include the busy-retry columns `retry_count INTEGER NOT NULL DEFAULT
0` and `next_retry_at TIMESTAMP NULL` — autogenerate produces them from the
SQLModel definition; **do not hand-edit** the generated migration to add them.

- [ ] **Step 2: Read the generated file and confirm it matches the model**

Open the generated file. Confirm: both `create_table` calls present, the unique
constraint name is `uq_stkrn_task_scheduled_for`, the partial index carries
`postgresql_where=sa.text('deleted_at IS NOT NULL')`, and `scheduled_task_runs`
includes `retry_count` (NOT NULL, server_default `'0'`) and `next_retry_at`
(nullable). If autogen produced an empty migration, the models aren't imported
into metadata — re-check Task 3 Step 2.

- [ ] **Step 3: Apply and round-trip the migration**

Run:
```bash
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: each command exits 0; the up→down→up cycle proves `downgrade()` drops
both tables cleanly.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(db): migration for scheduled_tasks tables"
```

---

### Task 5: Repositories — scoped CRUD + the SKIP LOCKED claim query

**Files:**
- Create: `backend/cubeplex/repositories/scheduled_task.py`
- Test: covered by the E2E firing test in Task 9 (the claim query needs a real
  Postgres connection; `SKIP LOCKED` cannot be simulated in SQLite, so per
  CLAUDE.md E2E-priority this is validated E2E, not unit-mocked).

**Defaults locked in (spec §Open Questions resolutions):**
- `claim_timeout = 2 min` — how long a row may sit in `claimed` (run_id NULL)
  before another poller may re-pick it after a dispatch crash.
- `max_claims = 3` — re-claim cap; past this the occurrence is set `failed`
  with a "max re-claims exceeded" reason instead of being retried forever.
- `busy_retry_delay = 5 min` and `max_busy_retries = 3` — used by the dispatch
  step (Task 6) when a fixed target conversation is busy: postpone the
  occurrence by 5 min, increment `retry_count`, leave the row re-claimable;
  past the cap, set terminal `skipped_busy_max_retries`.

These four constants are surfaced as `ScheduledTaskPoller.__init__` arguments
(Task 8) so tests can shorten them.

- [ ] **Step 1: Implement both repositories**

```python
# backend/cubeplex/repositories/scheduled_task.py
"""Repositories for scheduled tasks and their occurrence history.

``ScheduledTaskRunRepository.claim_due_tasks`` is the poller's hot path. It uses
``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent pollers on different replicas
never claim the same row; the caller advances ``next_fire_at`` and inserts the
history row in the SAME transaction so the claim is atomic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.repositories.base import ScopedRepository


class ScheduledTaskRepository(ScopedRepository[ScheduledTask]):
    model = ScheduledTask

    def _scoped_select(self) -> Any:
        return super()._scoped_select().where(
            cast(Any, ScheduledTask.deleted_at).is_(None)
        )

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
            .where(ScheduledTaskRun.scheduled_task_id == task_id)  # type: ignore[arg-type]
            .order_by(ScheduledTaskRun.scheduled_for.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


async def claim_due_tasks(session: AsyncSession, *, now: datetime, limit: int) -> list[ScheduledTask]:
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
            ScheduledTask.next_fire_at <= now,  # type: ignore[operator]
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

    A row with run_id NULL means a replica reserved the occurrence but died
    before start_run. Re-claimable. ``FOR UPDATE SKIP LOCKED`` again.
    """
    cutoff = now - claim_timeout
    stmt = (
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "claimed",  # type: ignore[arg-type]
            cast(Any, ScheduledTaskRun.run_id).is_(None),
            ScheduledTaskRun.claimed_at < cutoff,  # type: ignore[operator]
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def claim_busy_postponed_runs(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[ScheduledTaskRun]:
    """Lock and return occurrence rows postponed because the fixed target was busy.

    Rows in state 'claimed' with run_id IS NULL, next_retry_at IS NOT NULL,
    next_retry_at <= now, retry_count < 3. Caller increments retry_count when
    re-attempting dispatch (Task 8). ``FOR UPDATE SKIP LOCKED`` makes concurrent
    pollers safe.
    """
    stmt = (
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "claimed",  # type: ignore[arg-type]
            cast(Any, ScheduledTaskRun.run_id).is_(None),
            cast(Any, ScheduledTaskRun.next_retry_at).is_not(None),
            ScheduledTaskRun.next_retry_at <= now,  # type: ignore[operator]
            ScheduledTaskRun.retry_count < 3,  # type: ignore[operator]
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
    history is not stuck. (The completion hook only flips started->terminal for
    runs that actually complete; this covers the crashed-mid-run case.)
    Returns the number of rows failed.
    """
    cutoff = now - started_timeout
    stmt = (
        update(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.state == "started",  # type: ignore[arg-type]
            ScheduledTaskRun.started_at < cutoff,  # type: ignore[operator]
        )
        .values(state="failed", detail="run did not report terminal status in time",
                updated_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)
```

Add `from sqlalchemy import update` to the imports if not already present.

- [ ] **Step 2: Verify it imports**

Run: `uv run python -c "from cubeplex.repositories.scheduled_task import claim_due_tasks, claim_stale_runs, claim_busy_postponed_runs, fail_stale_started_runs, ScheduledTaskRepository, ScheduledTaskRunRepository; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/repositories/scheduled_task.py
git commit -m "feat(repos): scheduled-task repos + SKIP LOCKED claim queries"
```

---

### Task 6: Dispatch seam — resolve target + start_run

**Files:**
- Create: `backend/cubeplex/schedules/dispatch.py`

- [ ] **Step 1: Implement the dispatch seam**

```python
# backend/cubeplex/schedules/dispatch.py
"""The shared 'something decided to run an agent' seam.

``dispatch_scheduled_run`` resolves/creates the target conversation, builds a
``RunContext`` for the OWNER, and calls the same ``RunManager.start_run`` an
interactive message uses. #152 (triggers) and #153 (managed agents) reuse this.
"""

from __future__ import annotations

from dataclasses import dataclass

from cubeplex.db.engine import async_session_maker
from cubeplex.models.scheduled_task import ScheduledTask
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.membership import MembershipRepository
from cubeplex.streams.run_manager import RunContext, RunManager


class TargetUnavailableError(Exception):
    """Fixed target missing/not owner-owned, or owner lost membership."""


class ConversationBusyError(Exception):
    """Fixed target conversation already has a running run.

    The poller (Task 8) catches this and applies the busy-retry policy
    (spec §"One-run-per-conversation interaction"): postpone by 5m up to 3
    times, then terminal ``skipped_busy_max_retries``.
    """


@dataclass(slots=True)
class DispatchResult:
    run_id: str
    conversation_id: str


async def _owner_still_member(task: ScheduledTask) -> bool:
    async with async_session_maker() as session:
        role = await MembershipRepository(session).get_role(
            user_id=task.owner_user_id, workspace_id=task.workspace_id
        )
    return role is not None


async def resolve_target(task: ScheduledTask) -> str:
    """Return a conversation_id owned by the task owner, creating one if needed.

    For ``fixed``: re-validate ownership through the owner-scoped repo (the
    conversation may have been deleted/reassigned since create). For
    ``new_each_run``: create a fresh owner-owned conversation.
    Raises ``TargetUnavailableError`` if a fixed target is no longer the owner's.
    """
    async with async_session_maker() as session:
        repo = ConversationRepository(
            session,
            org_id=task.org_id,
            workspace_id=task.workspace_id,
            user_id=task.owner_user_id,
        )
        if task.target_mode == "fixed":
            if task.target_conversation_id is None:
                raise TargetUnavailableError("fixed target has no conversation id")
            conv = await repo.get_by_id(task.target_conversation_id)
            if conv is None:
                raise TargetUnavailableError("fixed target not found or not owner-owned")
            return conv.id
        conv = await repo.create(title=task.name)
        return conv.id


async def dispatch_scheduled_run(
    *, task: ScheduledTask, run_manager: RunManager
) -> DispatchResult:
    """Start one run for one occurrence.

    Raises:
      TargetUnavailableError -- owner is gone OR fixed target is missing /
        no longer owner-owned. The poller marks the occurrence ``failed``.
      ConversationBusyError -- ``fixed`` target already has a running run.
        The poller applies the busy-retry policy (postpone 5m, retry up to 3,
        then ``skipped_busy_max_retries``).
    """
    if not await _owner_still_member(task):
        raise TargetUnavailableError("owner is no longer a workspace member")
    conversation_id = await resolve_target(task)
    ctx = RunContext(
        user_id=task.owner_user_id,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
        agent_config_id=task.agent_config_id,  # see the decision note below
    )
    try:
        run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content=task.prompt,
            attachments=[],
            ctx=ctx,
        )
    except RuntimeError as exc:
        # RunManager.start_run rejects a second run on a conversation that
        # already has one running. For target_mode='fixed' this is the busy
        # case the spec's 5m-retry policy handles; surface it distinctly so
        # the poller can postpone instead of failing. For 'new_each_run' the
        # conversation is brand-new so this branch is effectively unreachable;
        # if it ever fires, fall through as a regular runtime error.
        if task.target_mode == "fixed" and "already" in str(exc).lower():
            raise ConversationBusyError(str(exc)) from exc
        raise
    return DispatchResult(run_id=run_id, conversation_id=conversation_id)
```

> **DECISION REQUIRED — `agent_config_id` must not be store-and-ignore.** Today
> `RunContext` (`backend/cubeplex/streams/run_manager.py:30`) has **only**
> `user_id` / `org_id` / `workspace_id`, and `_execute_run` always loads the
> *workspace-default* `AgentConfig` (lines ~1772-1797). So a `task.agent_config_id`
> the spec defines (design §4, "null = workspace default") is silently dropped.
> The dispatch above passes it, but that requires real plumbing — pick one
> before implementing this task and update the plan accordingly:
> 1. **Honor it (matches spec):** add an optional `agent_config_id: str | None`
>    to `RunContext`, and in `_execute_run` load the named `AgentConfig` when set
>    (scoped to the same `org_id`/`workspace_id`), falling back to the workspace
>    default when null/missing. This touches the LLM call path — read
>    `backend/docs/prompt-cache-discipline.md` first and keep the system-prompt
>    assembly cache-stable. Add an E2E that a task with a non-default
>    `agent_config_id` runs under that config.
> 2. **Defer it:** drop the `agent_config_id` column + field + body param from
>    this PR entirely (do not migrate a column nothing reads) and note in the
>    spec that per-task agent selection is a follow-up. v1 then always uses the
>    workspace default.
>
> Do **not** keep the column while leaving `RunContext`/`_execute_run` unchanged.

- [ ] **Step 2: Verify it imports**

Run: `uv run python -c "from cubeplex.schedules.dispatch import dispatch_scheduled_run, resolve_target, TargetUnavailableError, ConversationBusyError; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/schedules/dispatch.py
git commit -m "feat(schedules): dispatch seam reusing run_manager.start_run"
```

---

### Task 7: Run-completion hook (started → succeeded/failed)

**Files:**
- Create: `backend/cubeplex/schedules/completion_hook.py`
- Modify: `backend/cubeplex/streams/run_manager.py` (3 terminal-status sites)

- [ ] **Step 1: Implement the hook**

```python
# backend/cubeplex/schedules/completion_hook.py
"""Copy a run's terminal outcome back onto its scheduled_task_runs row.

Keyed by run_id: interactive runs (no matching row) are a no-op. Best-effort —
never raises into the run-finalization path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select, update

from cubeplex.db.engine import async_session_maker
from cubeplex.models.scheduled_task import ScheduledTaskRun

# RunManager status -> occurrence terminal state.
_TERMINAL_MAP = {"completed": "succeeded", "failed": "failed", "cancelled": "failed"}


async def record_scheduled_run_terminal_state(*, run_id: str, run_status: str) -> None:
    new_state = _TERMINAL_MAP.get(run_status)
    if new_state is None:
        return
    try:
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(ScheduledTaskRun.id).where(  # type: ignore[arg-type]
                        ScheduledTaskRun.run_id == run_id,  # type: ignore[arg-type]
                        ScheduledTaskRun.state == "started",  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            detail = None if new_state == "succeeded" else f"run {run_status}"
            await session.execute(
                update(ScheduledTaskRun)
                .where(ScheduledTaskRun.id == row)  # type: ignore[arg-type]
                .values(state=new_state, detail=detail, updated_at=datetime.now(UTC))
            )
            await session.commit()
    except Exception as exc:  # never break run finalization
        logger.warning("scheduled-run completion hook failed for {}: {}", run_id, exc)
```

- [ ] **Step 2: Call the hook at all three terminal-status sites**

In `backend/cubeplex/streams/run_manager.py` `_execute_run`, immediately after
each `await update_run_meta(..., status=...)` call (the `completed`, `cancelled`,
and `failed` blocks around lines 1895/1903/1921), add:
```python
from cubeplex.schedules.completion_hook import record_scheduled_run_terminal_state

await record_scheduled_run_terminal_state(run_id=run_id, run_status="completed")
```
(use `"cancelled"` and `"failed"` respectively at the other two sites). Place the
import at the top of `_execute_run` with the other local imports to avoid a
module-level import cycle.

- [ ] **Step 3: Verify import + run_manager still imports**

Run: `uv run python -c "import cubeplex.streams.run_manager; from cubeplex.schedules.completion_hook import record_scheduled_run_terminal_state; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/schedules/completion_hook.py backend/cubeplex/streams/run_manager.py
git commit -m "feat(schedules): run-completion hook writes occurrence terminal state"
```

---

### Task 8: The poller loop

**Files:**
- Create: `backend/cubeplex/schedules/poller.py`
- Modify: `backend/cubeplex/api/app.py` (start in lifespan, drain on shutdown)

- [ ] **Step 1: Implement the poller**

```python
# backend/cubeplex/schedules/poller.py
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

from loguru import logger
from sqlalchemy.exc import IntegrityError

from cubeplex.db.engine import async_session_maker
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.repositories.scheduled_task import (
    claim_busy_postponed_runs,
    claim_due_tasks,
    claim_stale_runs,
    fail_stale_started_runs,
)
from cubeplex.schedules.compute import (
    MissedDecision,
    as_utc,
    decide_missed,
    latest_due_before,
    next_fire_after,
)
from cubeplex.schedules.dispatch import (
    ConversationBusyError,
    TargetUnavailableError,
    dispatch_scheduled_run,
)
from cubeplex.streams.run_manager import RunManager


class ScheduledTaskPoller:
    def __init__(
        self,
        *,
        run_manager: RunManager,
        # OQ1: poll at 15s with jitter, minute-granularity v1.
        poll_interval_seconds: float = 15.0,
        jitter_seconds: float = 5.0,
        misfire_grace_seconds: int = 300,
        # OQ6: claim_timeout = 2 min, max_claims = 3 (accept rare double-fire).
        claim_timeout_seconds: int = 120,
        max_claims: int = 3,
        started_timeout_seconds: int = 3600,  # fail 'started' rows stuck > 1h
        # OQ3: busy-conversation postpone — 5 min delay, retry up to 3 times,
        # then terminal skipped_busy_max_retries.
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
        to_dispatch: list[str] = []  # scheduled_task_runs ids in state 'claimed'

        async with async_session_maker() as session:
            due = await claim_due_tasks(session, now=now, limit=self._batch_limit)
            for task in due:
                row_id = self._claim_occurrence(session, task=task, now=now)
                if row_id is not None:
                    to_dispatch.append(row_id)

            stale = await claim_stale_runs(
                session, now=now, claim_timeout=self._claim_timeout, limit=self._batch_limit
            )
            for row in stale:
                if row.claim_count >= self._max_claims:
                    row.state = "failed"
                    row.detail = "max re-claims exceeded"
                else:
                    row.claimed_at = now
                    row.claim_count += 1
                    to_dispatch.append(row.id)

            # OQ3 busy-retry pickup: rows postponed by an earlier dispatch
            # because the fixed target was busy. Re-pick once next_retry_at
            # has passed and retry_count is still under the cap; the cap is
            # enforced when we set next_retry_at in _dispatch_one (rows past
            # the cap go straight to skipped_busy_max_retries there).
            busy = await claim_busy_postponed_runs(
                session, now=now, limit=self._batch_limit
            )
            for row in busy:
                row.claimed_at = now
                # OQ3: retry_count was already incremented when the postpone
                # was recorded; clearing next_retry_at marks the row as a
                # fresh dispatch attempt and lets _dispatch_one see it as a
                # regular 'claimed' row.
                row.next_retry_at = None
                to_dispatch.append(row.id)

            # Recover rows stuck in 'started' (replica died after start_run but
            # before the completion hook). No live run to resume, so fail them
            # after a generous timeout instead of leaving history stuck forever.
            await fail_stale_started_runs(
                session, now=now, started_timeout=self._started_timeout,
                limit=self._batch_limit,
            )
            try:
                await session.commit()
            except IntegrityError:
                # Lost a (task_id, scheduled_for) race to another poller. The
                # claimed-but-not-committed rows roll back; nothing to dispatch.
                await session.rollback()
                return

        for row_id in to_dispatch:
            await self._dispatch_one(row_id)

    def _claim_occurrence(
        self, session, *, task: ScheduledTask, now: datetime
    ) -> str | None:
        """Insert/handle the occurrence row + advance next_fire_at in the txn.

        Returns the new claimed-row id to dispatch after commit, or None
        (skipped / advanced only).
        """
        candidate = task.next_fire_at
        assert candidate is not None
        candidate = as_utc(candidate)  # naive from DB; compute returns aware UTC
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
        skipped_older = latest_due > candidate  # there was a missed stretch
        decision = decide_missed(latest_due=latest_due, now=now, grace_seconds=self._grace)

        # Advance next_fire_at (same txn).
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
                    detail=f"missed beyond grace; latest_due={latest_due.isoformat()}",
                )
            )
            return None

        # FIRE: optional summary row for the older skipped stretch.
        if skipped_older:
            session.add(
                ScheduledTaskRun(
                    scheduled_task_id=task.id,
                    org_id=task.org_id,
                    workspace_id=task.workspace_id,
                    scheduled_for=candidate,
                    claimed_at=now,
                    state="skipped_missed",
                    detail=f"caught up: skipped {candidate.isoformat()}..{latest_due.isoformat()}",
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
            try:
                result = await dispatch_scheduled_run(task=task, run_manager=self._run_manager)
            except TargetUnavailableError as exc:
                row.state = "failed"
                row.detail = str(exc)
                await session.commit()
                return
            except ConversationBusyError as exc:
                # OQ3: fixed target conversation is busy. Postpone by
                # busy_retry_delay; bump retry_count. After max_busy_retries
                # the row is terminal skipped_busy_max_retries.
                if row.retry_count + 1 >= self._max_busy_retries:
                    row.state = "skipped_busy_max_retries"
                    row.retry_count = row.retry_count + 1
                    row.next_retry_at = None
                    row.detail = (
                        f"target conversation busy after "
                        f"{row.retry_count} retries: {exc}"
                    )
                else:
                    # Leave state='claimed' (re-claimable) but flag for the
                    # next poll cycle via next_retry_at.
                    row.retry_count = row.retry_count + 1
                    row.next_retry_at = datetime.now(UTC) + self._busy_retry_delay
                    row.detail = (
                        f"target conversation busy; retry {row.retry_count}"
                        f"/{self._max_busy_retries} at {row.next_retry_at.isoformat()}"
                    )
                await session.commit()
                return
            row.state = "started"
            row.run_id = result.run_id
            row.conversation_id = result.conversation_id
            row.started_at = datetime.now(UTC)
            await session.commit()
```

- [ ] **Step 2: Wire into lifespan**

In `backend/cubeplex/api/app.py`, after `run_manager` is created and
`_app.state.run_manager = run_manager` is set (around line 175), add:
```python
from cubeplex.schedules.poller import ScheduledTaskPoller
from cubeplex.config import config as _sched_cfg

poller = ScheduledTaskPoller(
    run_manager=run_manager,
    poll_interval_seconds=float(_sched_cfg.get("scheduled_tasks.poll_interval_seconds", 15.0)),
    misfire_grace_seconds=int(_sched_cfg.get("scheduled_tasks.misfire_grace_seconds", 300)),
    claim_timeout_seconds=int(_sched_cfg.get("scheduled_tasks.claim_timeout_seconds", 120)),
    max_claims=int(_sched_cfg.get("scheduled_tasks.max_claims", 3)),
    busy_retry_delay_seconds=int(_sched_cfg.get("scheduled_tasks.busy_retry_delay_seconds", 300)),
    max_busy_retries=int(_sched_cfg.get("scheduled_tasks.max_busy_retries", 3)),
)
poller.start()
_app.state.scheduled_task_poller = poller
```
In the shutdown phase, BEFORE `run_manager.drain(...)` (around line 344), add:
```python
poller = getattr(_app.state, "scheduled_task_poller", None)
if poller is not None:
    await poller.stop()
```

- [ ] **Step 3: Verify the app boots with the poller**

Run: `uv run python -c "import cubeplex.schedules.poller; from cubeplex.schedules.poller import ScheduledTaskPoller; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/schedules/poller.py backend/cubeplex/api/app.py
git commit -m "feat(schedules): per-replica poller + lifespan wiring"
```

---

### Task 9: Workspace routes (scope-isolated) + schemas + owner/admin auth

**Files:**
- Create: `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`
- Create: `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py` (re-export)
- Modify: `backend/cubeplex/api/app.py` (include router)

- [ ] **Step 1: Schemas**

```python
# backend/cubeplex/api/schemas/ws_scheduled_tasks.py
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, model_validator

# Enum-restrict the discriminators so an unknown value is a 422 at parse time,
# not a silent fall-through to wrong behavior downstream.
ScheduleKind = Literal["cron", "interval", "once"]
TargetMode = Literal["fixed", "new_each_run"]


def _validate_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {tz!r}") from exc


def _validate_cron(expr: str) -> None:
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")


class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
    schedule_kind: ScheduleKind
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str = "UTC"
    target_mode: TargetMode
    target_conversation_id: str | None = None
    agent_config_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ScheduledTaskCreate":
        _validate_timezone(self.timezone)
        if self.schedule_kind == "cron":
            if not self.cron_expr:
                raise ValueError("cron_expr required for cron schedule")
            _validate_cron(self.cron_expr)
        if self.schedule_kind == "interval" and not self.interval_seconds:
            raise ValueError("interval_seconds required for interval schedule")
        if self.schedule_kind == "once":
            if self.run_at is None:
                raise ValueError("run_at required for once schedule")
            if self.run_at.tzinfo is None:
                raise ValueError("run_at must include a timezone offset")
        if self.target_mode == "fixed" and not self.target_conversation_id:
            raise ValueError("target_conversation_id required when target_mode=fixed")
        return self


class ScheduledTaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    cron_expr: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at: datetime | None = None
    timezone: str | None = None
    target_mode: TargetMode | None = None
    target_conversation_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ScheduledTaskPatch":
        if self.timezone is not None:
            _validate_timezone(self.timezone)
        if self.cron_expr is not None:
            _validate_cron(self.cron_expr)
        if self.run_at is not None and self.run_at.tzinfo is None:
            raise ValueError("run_at must include a timezone offset")
        return self


class ScheduledTaskOut(BaseModel):
    id: str
    name: str
    status: str
    schedule_kind: str
    cron_expr: str | None
    interval_seconds: int | None
    run_at: str | None
    timezone: str
    prompt: str
    target_mode: str
    target_conversation_id: str | None
    owner_user_id: str
    next_fire_at: str | None
    last_fired_at: str | None
    created_at: str
    updated_at: str


class ScheduledTaskRunOut(BaseModel):
    id: str
    scheduled_for: str
    claimed_at: str
    started_at: str | None
    state: str
    # OQ3 busy-retry fields surfaced so the frontend can show why a fire was
    # postponed and what the terminal skipped_busy_max_retries state means.
    retry_count: int
    next_retry_at: str | None
    run_id: str | None
    conversation_id: str | None
    detail: str | None


class ScheduledTaskListOut(BaseModel):
    tasks: list[ScheduledTaskOut]
```

- [ ] **Step 2: Router with member-read / owner-or-admin-mutate**

```python
# backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py
"""Workspace scheduled-task routes. Scope-isolated: no admin/cross-ws variant.

Reads require membership; mutations (edit/pause/resume/delete) require being the
task owner OR a workspace admin — a scheduled run executes as the owner, so a
non-owner editing the prompt would run code under the owner's identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from cubeplex.api.schemas.ws_scheduled_tasks import (
    ScheduledTaskCreate,
    ScheduledTaskListOut,
    ScheduledTaskOut,
    ScheduledTaskPatch,
    ScheduledTaskRunOut,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db.engine import async_session_maker
from cubeplex.models import Role
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.scheduled_task import (
    ScheduledTaskRepository,
    ScheduledTaskRunRepository,
)
from cubeplex.schedules.compute import as_utc, latest_due_before, next_fire_after
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/scheduled-tasks", tags=["scheduled-tasks"])


def _iso(dt: datetime | None) -> str | None:
    return utc_isoformat(dt) if dt is not None else None


def _to_out(t: ScheduledTask) -> ScheduledTaskOut:
    return ScheduledTaskOut(
        id=t.id, name=t.name, status=t.status, schedule_kind=t.schedule_kind,
        cron_expr=t.cron_expr, interval_seconds=t.interval_seconds, run_at=_iso(t.run_at),
        timezone=t.timezone, prompt=t.prompt, target_mode=t.target_mode,
        target_conversation_id=t.target_conversation_id, owner_user_id=t.owner_user_id,
        next_fire_at=_iso(t.next_fire_at), last_fired_at=_iso(t.last_fired_at),
        created_at=utc_isoformat(t.created_at), updated_at=utc_isoformat(t.updated_at),
    )


def _initial_next_fire(t: ScheduledTask) -> datetime | None:
    now = datetime.now(UTC)
    if t.schedule_kind == "once":
        return t.run_at
    return next_fire_after(
        kind=t.schedule_kind, after=now, cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds, tz=t.timezone,
    )


def _resume_next_fire(session, t: ScheduledTask) -> datetime | None:
    """Resume policy: account for occurrences that fell due while paused.

    Spec §missed-run requires an explicit policy for paused stretches, mirroring
    the outage policy: run-latest-once-or-skip with at most ONE summary history
    row. We anchor on the occurrence that was pending when the task paused
    (preserved in next_fire_at — see pause_task, which no longer nulls it) and:
      - `once`: if run_at is in the past, record a single skipped_missed summary
        and return None (do not back-fire a one-shot after a pause).
      - cron/interval: compute latest_due_before(anchor, now); if a stretch was
        skipped, add ONE ScheduledTaskRun(state="skipped_missed") summary with a
        detail describing the range, then return next_fire_after(latest_due).
    Returns the next fire time to store (None for a spent `once`).
    """
    now = datetime.now(UTC)
    anchor = as_utc(t.next_fire_at) if t.next_fire_at is not None else None
    if t.schedule_kind == "once":
        run_at = as_utc(t.run_at) if t.run_at is not None else None
        if run_at is not None and run_at <= now:
            session.add(ScheduledTaskRun(
                scheduled_task_id=t.id, org_id=t.org_id, workspace_id=t.workspace_id,
                scheduled_for=run_at, claimed_at=now, state="skipped_missed",
                detail="paused past its one-shot fire time",
            ))
            return None
        return run_at
    if anchor is None or anchor > now:
        return anchor if anchor is not None else _initial_next_fire(t)
    latest_due = latest_due_before(
        kind=t.schedule_kind, candidate=anchor, now=now,
        cron_expr=t.cron_expr, interval_seconds=t.interval_seconds, tz=t.timezone,
    )
    session.add(ScheduledTaskRun(
        scheduled_task_id=t.id, org_id=t.org_id, workspace_id=t.workspace_id,
        scheduled_for=anchor, claimed_at=now, state="skipped_missed",
        detail=f"paused: skipped {anchor.isoformat()}..{latest_due.isoformat()}",
    ))
    return next_fire_after(
        kind=t.schedule_kind, after=latest_due, cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds, tz=t.timezone,
    )


async def _load_for_mutation(ctx: RequestContext, task_id: str) -> ScheduledTask:
    """Load task (404 if missing) and enforce owner-or-admin (403 otherwise)."""
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = await repo.get_active(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
    if task.owner_user_id != ctx.user.id and ctx.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner or admin required")
    return task


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScheduledTaskOut)
async def create_task(
    body: ScheduledTaskCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        # Validate fixed target is owned by the creator (the run identity).
        if body.target_mode == "fixed":
            conv_repo = ConversationRepository(
                session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user.id
            )
            if await conv_repo.get_by_id(body.target_conversation_id or "") is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "target_conversation_id must be your own conversation",
                )
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = ScheduledTask(
            org_id=ctx.org_id, workspace_id=ctx.workspace_id, owner_user_id=ctx.user.id,
            name=body.name, prompt=body.prompt, schedule_kind=body.schedule_kind,
            cron_expr=body.cron_expr, interval_seconds=body.interval_seconds,
            run_at=body.run_at, timezone=body.timezone, target_mode=body.target_mode,
            target_conversation_id=body.target_conversation_id,
            agent_config_id=body.agent_config_id, status="active",
        )
        task.next_fire_at = _initial_next_fire(task)
        task = await repo.create(task)
        return _to_out(task)


@router.get("", response_model=ScheduledTaskListOut)
async def list_tasks(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskListOut:
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        tasks = await repo.list_all()
    return ScheduledTaskListOut(tasks=[_to_out(t) for t in tasks])


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = await repo.get_active(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
    return _to_out(task)


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def patch_task(
    task_id: str,
    body: ScheduledTaskPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = await repo.get_active(task_id)
        assert task is not None
        if body.target_mode == "fixed" or (
            body.target_conversation_id is not None and task.target_mode == "fixed"
        ):
            conv_repo = ConversationRepository(
                session, org_id=ctx.org_id, workspace_id=ctx.workspace_id,
                user_id=task.owner_user_id,
            )
            target = body.target_conversation_id or task.target_conversation_id
            if target is None or await conv_repo.get_by_id(target) is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "target_conversation_id must be the owner's conversation",
                )
        for field in ("name", "prompt", "cron_expr", "interval_seconds", "run_at",
                      "timezone", "target_mode", "target_conversation_id"):
            val = getattr(body, field)
            if val is not None:
                setattr(task, field, val)
        task.next_fire_at = _initial_next_fire(task) if task.status == "active" else None
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.post("/{task_id}/pause", response_model=ScheduledTaskOut)
async def pause_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = await repo.get_active(task_id)
        assert task is not None
        task.status = "paused"
        # Keep next_fire_at as the resume anchor (the occurrence pending at pause
        # time) so resume can apply the missed-run policy to the paused stretch.
        # The poller never fires a paused task (claim_due_tasks filters
        # status='active'), so leaving the anchor set is safe.
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.post("/{task_id}/resume", response_model=ScheduledTaskOut)
async def resume_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ScheduledTaskOut:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        task = await repo.get_active(task_id)
        assert task is not None
        task.status = "active"
        # Apply the spec's missed-run policy to the paused stretch instead of
        # silently dropping it. Reuse latest_due_before from an anchor (the last
        # fire, else the original anchor) to find occurrences that fell due while
        # paused; if any, record ONE skipped_missed summary row (never one per
        # occurrence — same as the outage policy, spec §missed-run) before
        # fast-forwarding next_fire_at. See the resume policy note below.
        task.next_fire_at = _resume_next_fire(session, task)  # records summary if needed
        task.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
        return _to_out(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    await _load_for_mutation(ctx, task_id)
    async with async_session_maker() as session:
        repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        await repo.soft_delete(task_id)


@router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunOut])
async def list_task_runs(
    task_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> list[ScheduledTaskRunOut]:
    async with async_session_maker() as session:
        task_repo = ScheduledTaskRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        if await task_repo.get_active(task_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled task not found")
        run_repo = ScheduledTaskRunRepository(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        rows = await run_repo.list_for_task(task_id)
    return [
        ScheduledTaskRunOut(
            id=r.id, scheduled_for=utc_isoformat(r.scheduled_for),
            claimed_at=utc_isoformat(r.claimed_at), started_at=_iso(r.started_at),
            state=r.state, retry_count=r.retry_count, next_retry_at=_iso(r.next_retry_at),
            run_id=r.run_id, conversation_id=r.conversation_id, detail=r.detail,
        )
        for r in rows
    ]
```

- [ ] **Step 3: Register the router**

In `backend/cubeplex/api/routes/v1/__init__.py`, add `ws_scheduled_tasks` to the
import block and to `__all__` (alphabetically near `ws_sandbox_env`). In
`backend/cubeplex/api/app.py`, add `ws_scheduled_tasks` to the
`from cubeplex.api.routes.v1 import (...)` tuple (around line 444) and add:
```python
app.include_router(ws_scheduled_tasks.router, prefix="/api/v1")
```
next to the other `ws_*` includes.

- [ ] **Step 4: Verify the app imports the router**

Run: `uv run python -c "from cubeplex.api.routes.v1 import ws_scheduled_tasks; print([r.path for r in ws_scheduled_tasks.router.routes])"`
Expected: lists the 8 paths (`POST ''`, `GET ''`, `GET /{task_id}`, `PATCH`, pause, resume, delete, `/{task_id}/runs`).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/schemas/ws_scheduled_tasks.py backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py backend/cubeplex/api/routes/v1/__init__.py backend/cubeplex/api/app.py
git commit -m "feat(api): workspace scheduled-task routes (member read, owner/admin mutate)"
```

---

### Task 10: CRUD + auth E2E

**Files:**
- Test: `backend/tests/e2e/test_scheduled_tasks_api.py`

- [ ] **Step 1: Write the E2E tests**

```python
# backend/tests/e2e/test_scheduled_tasks_api.py
"""E2E: scheduled-task CRUD, validation, pause/resume, auth."""

import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


def _make(client: TestClient, **over):
    body = {
        "name": "report",
        "prompt": "Summarize today",
        "schedule_kind": "interval",
        "interval_seconds": 3600,
        "target_mode": "new_each_run",
    }
    body.update(over)
    return client.post(BASE, json=body)


class TestScheduledTaskCRUD:
    def test_create_interval_sets_next_fire(self, client: TestClient) -> None:
        r = _make(client)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "active"
        assert data["next_fire_at"] is not None
        assert "+00:00" in data["next_fire_at"]  # utc_isoformat carries offset

    def test_create_cron_requires_expr(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", interval_seconds=None)
        assert r.status_code == 422

    def test_create_fixed_requires_owned_conversation(self, client: TestClient) -> None:
        r = _make(client, target_mode="fixed", target_conversation_id="conv-doesnotexist")
        assert r.status_code == 422

    def test_list_and_get(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        assert tid in {t["id"] for t in client.get(BASE).json()["tasks"]}
        assert client.get(f"{BASE}/{tid}").json()["id"] == tid

    def test_pause_keeps_anchor_resume_stays_active(self, client: TestClient) -> None:
        # Pause preserves next_fire_at as the resume anchor (the poller ignores
        # paused tasks). Resume keeps it active with a next fire scheduled.
        tid = _make(client).json()["id"]
        paused = client.post(f"{BASE}/{tid}/pause").json()
        assert paused["status"] == "paused"
        assert paused["next_fire_at"] is not None  # anchor retained, not nulled
        resumed = client.post(f"{BASE}/{tid}/resume").json()
        assert resumed["status"] == "active" and resumed["next_fire_at"] is not None

    def test_patch_prompt(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"prompt": "New prompt"})
        assert r.status_code == 200 and r.json()["prompt"] == "New prompt"

    def test_delete_then_404(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        assert client.delete(f"{BASE}/{tid}").status_code == 204
        assert client.get(f"{BASE}/{tid}").status_code == 404

    def test_runs_empty_for_new_task(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.get(f"{BASE}/{tid}/runs")
        assert r.status_code == 200 and r.json() == []


class TestScheduledTaskValidation:
    """Bad input must 422, not 500 or silently-wrong behavior (spec validation)."""

    def test_unknown_schedule_kind_422(self, client: TestClient) -> None:
        assert _make(client, schedule_kind="weekly").status_code == 422

    def test_unknown_target_mode_422(self, client: TestClient) -> None:
        assert _make(client, target_mode="broadcast").status_code == 422

    def test_invalid_timezone_422(self, client: TestClient) -> None:
        assert _make(client, timezone="Mars/Phobos").status_code == 422

    def test_invalid_cron_expr_422(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", cron_expr="not a cron",
                  interval_seconds=None)
        assert r.status_code == 422

    def test_once_requires_aware_run_at_422(self, client: TestClient) -> None:
        # A naive run_at (no offset) is ambiguous; reject it.
        r = _make(client, schedule_kind="once", interval_seconds=None,
                  run_at="2030-01-01T00:00:00")
        assert r.status_code == 422

    def test_interval_below_minimum_422(self, client: TestClient) -> None:
        assert _make(client, interval_seconds=30).status_code == 422

    def test_bad_agent_config_id_422(self, client: TestClient) -> None:
        # Only relevant if agent_config_id is honored (see Task 6 decision); if
        # the field is deferred/removed, delete this test with it.
        r = _make(client, agent_config_id="ac-doesnotexist")
        assert r.status_code in (404, 422)


class TestScheduledTaskAuth:
    """Owner/admin mutation gating + fixed-target ownership (spec §auth)."""

    def test_non_owner_member_cannot_mutate_403(
        self, client: TestClient, member_client: TestClient
    ) -> None:
        # Owner (admin `client`) creates; a different non-admin member is 403 on
        # pause/patch/delete but 200 on read (member-readable).
        tid = _make(client).json()["id"]
        assert member_client.get(f"{BASE}/{tid}").status_code == 200
        assert member_client.post(f"{BASE}/{tid}/pause").status_code == 403
        assert member_client.patch(f"{BASE}/{tid}", json={"prompt": "x"}).status_code == 403
        assert member_client.delete(f"{BASE}/{tid}").status_code == 403

    def test_admin_can_mutate_others_task(
        self, client: TestClient, member_client: TestClient
    ) -> None:
        # A task owned by a member can be paused/deleted by an admin.
        tid = _make(member_client).json()["id"]
        assert client.post(f"{BASE}/{tid}/pause").status_code == 200

    def test_fixed_target_must_be_owners_conversation_422(
        self, client: TestClient, member_client: TestClient
    ) -> None:
        # A fixed target pointing at a conversation owned by a *different* user
        # is rejected (the run executes as the owner; cross-user target leaks).
        other_conv = member_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "theirs"}
        ).json()["id"]
        r = _make(client, target_mode="fixed", target_conversation_id=other_conv)
        assert r.status_code == 422
```

> Auth tests need a second non-admin member in `DEFAULT_WS_ID`. If the e2e
> conftest has no `member_client` fixture, add one (register a user, add them to
> the workspace as a plain member, log in) mirroring the existing `client`
> fixture; reuse it across the auth cases.

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/e2e/test_scheduled_tasks_api.py -v`
Expected: PASS — all CRUD, validation, and auth cases green. (The `client`
fixture logs in as admin of `DEFAULT_WS_ID`; the conftest auto-routes to the
worktree test DB.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_scheduled_tasks_api.py
git commit -m "test(e2e): scheduled-task CRUD + validation + pause/resume"
```

---

### Task 11: Firing / missed-run / concurrency E2E

These drive `poller.poll_once()` directly (deterministic, no waiting on the
background loop) against the real per-slot Postgres so `FOR UPDATE SKIP LOCKED`
and the unique constraint are exercised for real.

**Files:**
- Test: `backend/tests/e2e/test_scheduled_tasks_firing.py`

- [ ] **Step 1: Write the firing tests**

```python
# backend/tests/e2e/test_scheduled_tasks_firing.py
"""E2E: poller fires runs, applies missed-run policy, never double-fires."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.schedules.poller import ScheduledTaskPoller
from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


def _create_due_once(client: TestClient) -> str:
    """Create a 'once' task and back-date next_fire_at so it is due now."""
    tid = client.post(
        BASE,
        json={
            "name": "fire-me", "prompt": "hi", "schedule_kind": "once",
            "run_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
            "target_mode": "new_each_run",
        },
    ).json()["id"]
    return tid


async def _runs_for(task_id: str) -> list[ScheduledTaskRun]:
    async with async_session_maker() as s:
        rows = (
            await s.execute(
                select(ScheduledTaskRun).where(
                    ScheduledTaskRun.scheduled_task_id == task_id  # type: ignore[arg-type]
                )
            )
        ).scalars().all()
        return list(rows)


@pytest.mark.asyncio
async def test_once_task_fires_one_run(client: TestClient) -> None:
    tid = _create_due_once(client)
    poller = ScheduledTaskPoller(
        run_manager=client.app.state.run_manager, misfire_grace_seconds=300
    )
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert len(rows) == 1
    assert rows[0].state in {"started", "succeeded", "failed"}
    assert rows[0].run_id is not None
    # 'once' task next_fire cleared.
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is None


@pytest.mark.asyncio
async def test_missed_beyond_grace_skips_and_fast_forwards(client: TestClient) -> None:
    # Hourly interval, next_fire 3h ago → latest_due ~ within last hour but
    # we force a tiny grace so it records skipped_missed and fast-forwards.
    tid = client.post(
        BASE,
        json={
            "name": "hourly", "prompt": "hi", "schedule_kind": "interval",
            "interval_seconds": 3600, "target_mode": "new_each_run",
        },
    ).json()["id"]
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(hours=3)
        await s.commit()
    poller = ScheduledTaskPoller(
        run_manager=client.app.state.run_manager, misfire_grace_seconds=1
    )
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert any(r.state == "skipped_missed" for r in rows)
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is not None
        assert task.next_fire_at > datetime.now(UTC) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_stale_started_run_is_failed(client: TestClient) -> None:
    # A row stuck in 'started' (replica died before the completion hook) past
    # the started_timeout must be marked failed, not left stuck forever.
    tid = _create_due_once(client)
    poller = ScheduledTaskPoller(
        run_manager=client.app.state.run_manager, started_timeout_seconds=1
    )
    await poller.poll_once()  # creates + dispatches -> state 'started'
    async with async_session_maker() as s:
        rows = await _runs_for(tid)
        row = rows[0]
        # Back-date started_at past the 1s timeout.
        db_row = await s.get(ScheduledTaskRun, row.id)
        assert db_row is not None
        db_row.state = "started"
        db_row.started_at = datetime.now(UTC) - timedelta(minutes=5)
        await s.commit()
    await poller.poll_once()  # the recovery sweep should fail it
    rows = await _runs_for(tid)
    assert rows[0].state == "failed"


@pytest.mark.asyncio
async def test_paused_stretch_records_skipped_missed_on_resume(client: TestClient) -> None:
    # Pause keeps the anchor; resuming after the anchor fell due records ONE
    # skipped_missed summary (not silent drop) and fast-forwards next_fire_at.
    tid = client.post(
        BASE,
        json={
            "name": "hourly", "prompt": "hi", "schedule_kind": "interval",
            "interval_seconds": 3600, "target_mode": "new_each_run",
        },
    ).json()["id"]
    client.post(f"{BASE}/{tid}/pause")
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(hours=3)  # fell due while paused
        await s.commit()
    client.post(f"{BASE}/{tid}/resume")
    rows = await _runs_for(tid)
    assert sum(1 for r in rows if r.state == "skipped_missed") == 1
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is not None
        assert task.next_fire_at > datetime.now(UTC) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_concurrent_pollers_fire_once(client: TestClient) -> None:
    tid = _create_due_once(client)
    p1 = ScheduledTaskPoller(run_manager=client.app.state.run_manager)
    p2 = ScheduledTaskPoller(run_manager=client.app.state.run_manager)
    await asyncio.gather(p1.poll_once(), p2.poll_once())
    rows = await _runs_for(tid)
    # Unique (task_id, scheduled_for) ⇒ exactly one occurrence row.
    assert len(rows) == 1
```

- [ ] **Step 2: Run the firing tests**

Run: `uv run pytest tests/e2e/test_scheduled_tasks_firing.py -v`
Expected: PASS — 5 passed (once-fires, missed-grace, stale-started recovery,
paused-stretch summary, concurrent-once). Runs hit the real cubepi path via
`start_run`; if a provider key is absent the run may end `failed`, which still
satisfies the `state in {"started","succeeded","failed"}` assertion and
`run_id is not None`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_scheduled_tasks_firing.py
git commit -m "test(e2e): scheduled-task firing, missed-run, concurrency"
```

---

### Task 12: Frontend — scope-isolated scheduled-tasks page (spec requirement)

The spec requires a scope-isolated workspace **page**, not just the API
("Scope-isolated workspace API and page"; "Frontend gets its own Next route +
page file"). This task adds it. Follow the `w/[wsId]/conversations` feature as
the structural template; obey the scope-isolated-pages rule (own route + page
file, modules are the reuse boundary, no `mode?` prop).

**Files (mirror exact conventions of the existing conversations feature):**
- Create: `frontend/packages/core/src/types/scheduled-task.ts` — request/response
  types matching the backend schemas (`ScheduledTaskOut`, run shape, create/patch).
- Create: the api client + `useScheduledTasks` / mutation hooks under
  `frontend/packages/core/src/api/` + `.../hooks/`.
- Create: `frontend/packages/web/app/api/v1/ws/[wsId]/scheduled-tasks/route.ts`
  and `[id]/route.ts`, `[id]/pause/route.ts`, `[id]/resume/route.ts`,
  `[id]/runs/route.ts` — SSE-safe proxy handlers (keep `compress: false`).
- Create: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/page.tsx` and
  its module components (list, detail/runs, create/edit form, pause/resume/delete
  controls). Gate mutate controls to owner-or-admin to match the API.
- Modify: workspace nav to add the "Scheduled tasks" entry.

- [ ] **Step 1: Types + core build.** Add the shared types and build
  `@cubeplex/core` (`pnpm build` in the core package) so the web app sees them.
- [ ] **Step 2: Proxy routes.** Add the `app/api/.../scheduled-tasks` handlers
  mirroring the conversations proxy; verify each forwards method + body + auth.
- [ ] **Step 3: Page + modules.** Build the page assembling list/detail/form
  modules. Match the project's design quality bar (shadcn components, polish).
  The run-history sub-view must surface the busy-retry path (spec OQ3): show
  `retry_count` and `next_retry_at` (when a fire was postponed because its
  fixed target conversation was busy), and render the terminal
  `skipped_busy_max_retries` state with its `detail` so a user can see why a
  fire never started. Treat the other terminal states (`succeeded`, `failed`,
  `skipped_missed`) the same way — state badge + detail.
- [ ] **Step 4: Verify.** `pnpm lint && pnpm type-check` clean; a Playwright
  smoke (create → see in list → pause → resume → delete) under the existing e2e
  harness. Keep selectors scoped per the project's e2e conventions.
- [ ] **Step 5: Commit** the frontend changes in one commit scoped `(#150)`.

> If the agent_config_id Task 6 decision is "honor it", surface an agent-config
> selector in the create/edit form; if "defer", omit it from the UI too.

---

### Task 13: Pre-PR sweep — full type + lint + targeted test run

**Files:** none (verification only).

- [ ] **Step 1: mypy strict + ruff**

Run (from `backend/`):
```bash
uv run mypy cubeplex/schedules cubeplex/models/scheduled_task.py cubeplex/repositories/scheduled_task.py cubeplex/api/routes/v1/ws_scheduled_tasks.py cubeplex/api/schemas/ws_scheduled_tasks.py
uv run ruff check cubeplex/schedules cubeplex/api/routes/v1/ws_scheduled_tasks.py
```
Expected: `Success: no issues found` and ruff clean (no findings). Fix any
100-char line-length or typing issues inline.

- [ ] **Step 1b: Frontend checks**

Run (from `frontend/`): `pnpm build` (build `@cubeplex/core` first), then
`pnpm lint && pnpm type-check`. Expected: clean.

- [ ] **Step 2: Run all new tests together**

Run:
```bash
uv run pytest tests/unit/test_schedule_compute.py tests/e2e/test_scheduled_tasks_api.py tests/e2e/test_scheduled_tasks_firing.py -v
```
Expected: all pass (7 unit + CRUD/validation/auth + 5 firing).

- [ ] **Step 3: Confirm migration is current**

Run: `uv run alembic check`
Expected: `No new upgrade operations detected.` (the model and DB schema agree).

- [ ] **Step 4: Commit any sweep fixes**

```bash
git add -A backend/cubeplex backend/tests
git commit -m "chore(schedules): mypy/ruff sweep fixes"
```
(Skip this commit if Steps 1–3 produced no changes.)

---

## Self-Review

**Spec coverage check** (each requirement → task):

- DB-backed poller with `FOR UPDATE SKIP LOCKED` → Task 5 (`claim_due_tasks` /
  `claim_stale_runs` use `.with_for_update(skip_locked=True)`), Task 8.
- Occurrence claim state machine `claimed→started→succeeded/failed`, re-claim on
  crash → Task 8 (`_claim_occurrence` inserts `claimed`; `_dispatch_one` → `started`;
  stale re-claim via `claim_stale_runs` + `claim_count`/`max_claims`), Task 7
  (terminal). 
- Unique `(task_id, scheduled_for)` idempotency key → Task 3 (`UniqueConstraint
  uq_stkrn_task_scheduled_for`), Task 8 (`IntegrityError` rollback), Task 11
  concurrency test.
- Poller filters `deleted_at IS NULL` → Task 5 (`claim_due_tasks` WHERE clause),
  Task 3 (model) and `soft_delete` nulls `next_fire_at`.
- Catch-up fires only `latest_due`, no per-occurrence backfill → Task 2
  (`latest_due_before`), Task 8 (single summary `skipped_missed` row), Task 11
  missed-run test.
- Owner-or-admin auth for edit/pause/delete; member read → Task 9
  (`_load_for_mutation` 403 unless owner or `Role.ADMIN`; reads use
  `require_member`).
- Fixed target must be owner's conversation → Task 9 (create + patch validate via
  owner-scoped `ConversationRepository`), Task 6 (`resolve_target` re-validates at
  dispatch; raises `TargetUnavailableError` → occurrence `failed`).
- Terminal state via run-completion hook reading run metadata → Task 7 (hook at
  the three `update_run_meta` terminal sites, keyed on `run_id`).
- New table → public_id prefix → Task 3 (`_PREFIX = "stask"` / `"stkrn"` on the
  models; no new constant needed in `public_id.py` since prefixes are per-model,
  matching `conv`/`sbx`).
- Three schedule kinds (cron/interval/once) → Task 2 + Task 9 validators.
- Reuse `start_run`, no fork → Task 6.
- Owner-left-workspace skip → Task 6 (`_owner_still_member` → `TargetUnavailableError`
  → occurrence `failed`).
- Busy fixed conversation → 5m postpone, retry up to 3 times, then terminal
  `skipped_busy_max_retries` (OQ3 resolution) → Task 6
  (`ConversationBusyError` from `dispatch_scheduled_run`), Task 8
  (`_dispatch_one` postpone branch + `claim_busy_postponed_runs` pickup).
- Run history read API → Task 9 (`GET /{task_id}/runs`).
- Poller started on every replica in lifespan + drained on shutdown → Task 8.
- Naive DB datetimes normalized before schedule arithmetic → Task 2 (`as_utc`
  at compute boundaries + poller), Task 2 naive-datetime regression test.
- Scope-isolated workspace **page** (own Next route + page file) → Task 12.
- Paused-stretch missed-run policy (one `skipped_missed` summary on resume, not
  a silent drop) → Task 9 (`_resume_next_fire`, pause keeps the anchor), Task 11
  paused-stretch test.
- Stale `started` rows recovered (replica died before completion hook) → Task 5
  (`fail_stale_started_runs`), Task 8 sweep, Task 11 stale-started test.
- Input validation (unknown kind/mode, bad timezone, bad cron, naive `run_at`,
  sub-minimum interval) is 422 not 500 → Task 9 schema validators, Task 10
  `TestScheduledTaskValidation`.
- Auth coverage (non-owner member 403, admin mutates other's task, fixed target
  owned by another user) → Task 10 `TestScheduledTaskAuth`.
- `agent_config_id` is honored end-to-end OR dropped — never store-and-ignore →
  Task 6 decision note (thread through `RunContext`/`_execute_run`, or remove the
  column from this PR).

**Note on `public_id.py`:** the spec and CLAUDE.md mention adding a prefix there.
Per the codebase convention (Task 3, confirmed against `conversation.py` /
`user_sandbox.py`), per-table prefixes are declared as `_PREFIX` ClassVars on the
model, NOT as module constants in `public_id.py` (those constants are only for
non-`CubeplexBase` tables like memory/sandbox-env). So no edit to `public_id.py`
is required; this is the correct interpretation of the "new table → public_id
prefix" rule, not a gap.

**Open Questions (spec §Open Questions):** all eight resolved 2026-05-28; the
session-resolved defaults are baked into this plan (poll 15s+jitter,
`misfire_grace=300s`, `claim_timeout=120s`, `max_claims=3`,
`busy_retry_delay=300s`, `max_busy_retries=3`, owner-leaves auto-pauses task,
no per-task cost cap — cost protection deferred to the project-wide
`CostMiddleware`, no scheduler-level per-user serialization). See the spec's
Open Questions section for the rationale on each.

**Placeholder scan:** no TBD/TODO/"handle edge cases" steps; every code step
contains complete code. **Type consistency:** `MissedDecision`, `next_fire_after`,
`latest_due_before`, `decide_missed`, `DispatchResult`, `TargetUnavailableError`,
`ConversationBusyError`, `ScheduledTaskPoller.poll_once`,
`record_scheduled_run_terminal_state`,
`claim_due_tasks`/`claim_stale_runs`/`claim_busy_postponed_runs` signatures
are referenced identically across Tasks 2/5/6/7/8/9/11.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
