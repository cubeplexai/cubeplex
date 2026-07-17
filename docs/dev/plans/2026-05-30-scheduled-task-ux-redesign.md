# Scheduled Task UX Redesign + Cron Validation Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 6-field cron validation bug, add an `end_at` deadline field, and replace the raw-cron form UI with a frequency-based visual picker.

**Architecture:** Backend tasks (1–5) add the `end_at` column, enforce it in the poller's every dispatch path, and tighten cron validation. Frontend tasks (6–10) introduce a `ScheduleEditorValue` state type, pure conversion utilities in `schedulePayload.ts`, and a `ScheduleEditor` component that replaces all raw-field inputs in the dialog. Backend and frontend can be PRed separately.

**Tech Stack:** Python/FastAPI/SQLModel/Alembic/croniter (backend), Next.js 15 / React 19 / TypeScript / Tailwind (frontend).

---

> **Before starting:** Run `./scripts/new-worktree feat/scheduled-task-ux` from the repo root and `cat .worktree.env` inside the worktree to get the allocated ports.

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `backend/cubeplex/api/schemas/ws_scheduled_tasks.py` | modify | 5-field cron validation, `end_at` in Create/Patch/Out |
| `backend/cubeplex/models/scheduled_task.py` | modify | `end_at` column |
| `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py` | modify | `end_at` in `_to_out`, create, PATCH clearing |
| `backend/cubeplex/repositories/scheduled_task.py` | modify | `end_at` filter in `claim_due_tasks` |
| `backend/cubeplex/schedules/poller.py` | modify | expired guard in `_dispatch_one` |
| `backend/alembic/versions/<hash>_add_end_at.py` | generated | alembic migration |
| `backend/tests/unit/test_scheduled_task_schemas.py` | create | unit tests for cron validation |
| `backend/tests/e2e/test_scheduled_tasks_api.py` | modify | `end_at` round-trip, PATCH clear, 6-field rejection |
| `frontend/packages/core/src/types/scheduled-task.ts` | modify | add `end_at` to all interfaces |
| `frontend/packages/web/…/scheduled-tasks/lib/schedulePayload.ts` | create | types + `buildSchedulePayload` + `parseSchedulePayload` |
| `frontend/packages/web/…/scheduled-tasks/components/ScheduleEditor.tsx` | create | frequency pills + all sub-field UI |
| `frontend/packages/web/…/scheduled-tasks/components/ScheduledTaskFormDialog.tsx` | modify | swap out raw fields for `ScheduleEditor` |
| `frontend/packages/web/__tests__/e2e/scheduled-tasks.spec.ts` | modify | smoke-test new form flow |

---

### Task 1: 5-field cron validation + DB data fix

**Files:**
- Modify: `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`
- Create: `backend/tests/unit/test_scheduled_task_schemas.py`

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_scheduled_task_schemas.py`:

```python
"""Unit tests for ScheduledTaskCreate / ScheduledTaskPatch validation."""
import pytest
from pydantic import ValidationError

from cubeplex.api.schemas.ws_scheduled_tasks import ScheduledTaskCreate

pytestmark = pytest.mark.unit

_BASE = dict(
    name="t",
    prompt="p",
    target_mode="new_each_run",
)


def _cron(**kw):
    return {**_BASE, "schedule_kind": "cron", **kw}


def test_6_field_cron_rejected():
    with pytest.raises(ValidationError, match="5 fields"):
        ScheduledTaskCreate(**_cron(cron_expr="0 9 * * * *"))


def test_4_field_cron_rejected():
    with pytest.raises(ValidationError, match="5 fields"):
        ScheduledTaskCreate(**_cron(cron_expr="9 * * *"))


def test_5_field_cron_accepted():
    obj = ScheduledTaskCreate(**_cron(cron_expr="0 9 * * *", timezone="Asia/Shanghai"))
    assert obj.cron_expr == "0 9 * * *"


def test_cron_with_l_dom_accepted():
    obj = ScheduledTaskCreate(**_cron(cron_expr="0 9 L * *", timezone="UTC"))
    assert obj.cron_expr == "0 9 L * *"
```

- [ ] **Step 2: Run and confirm FAIL**

```bash
cd backend
uv run pytest tests/unit/test_scheduled_task_schemas.py -v
```

Expected: `FAILED` — `ValidationError` not raised.

- [ ] **Step 3: Add field-count guard to `_validate_cron`**

In `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`, replace:

```python
def _validate_cron(expr: str) -> None:
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")
```

with:

```python
def _validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields "
            f"(minute hour day month weekday), got {len(parts)}: {expr!r}"
        )
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")
```

- [ ] **Step 4: Run and confirm PASS**

```bash
uv run pytest tests/unit/test_scheduled_task_schemas.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Fix the production DB record**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d cubeplex -c \
  "UPDATE scheduled_tasks SET cron_expr = '0 9 * * *' WHERE id = 'stask-1fTaVNGHVFa5CE';"
```

Expected: `UPDATE 1`

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/schemas/ws_scheduled_tasks.py \
        backend/tests/unit/test_scheduled_task_schemas.py
git commit -m "fix(scheduled-tasks): reject 6-field cron expressions; fix production record"
```

---

### Task 2: `end_at` model field + Alembic migration

**Files:**
- Modify: `backend/cubeplex/models/scheduled_task.py`
- Generated: `backend/alembic/versions/<hash>_add_end_at_to_scheduled_tasks.py`

- [ ] **Step 1: Add `end_at` field to `ScheduledTask`**

In `backend/cubeplex/models/scheduled_task.py`, after the `deleted_at` field:

```python
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    end_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
```

- [ ] **Step 2: Generate the migration**

```bash
cd backend
uv run alembic revision --autogenerate -m "add end_at to scheduled_tasks"
```

Expected: prints `Generating .../alembic/versions/<hash>_add_end_at_to_scheduled_tasks.py`.

- [ ] **Step 3: Run the migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade … -> <hash>`.

- [ ] **Step 4: Verify the column exists**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d cubeplex \
  -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='scheduled_tasks' AND column_name='end_at';"
```

Expected: one row — `end_at | timestamp with time zone`.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/scheduled_task.py backend/alembic/versions/
git commit -m "feat(scheduled-tasks): add end_at column (nullable timestamptz)"
```

---

### Task 3: API schema + route handler for `end_at`

**Files:**
- Modify: `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py`

- [ ] **Step 1: Add `end_at` to all three schemas**

In `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`:

In `ScheduledTaskCreate`, after `target_conversation_id`:
```python
    end_at: datetime | None = None
```

Add to the `_check` validator — `run_at` and `end_at` share the same tz-aware rule:
```python
        if self.end_at is not None and self.end_at.tzinfo is None:
            raise ValueError("end_at must include a timezone offset")
```

In `ScheduledTaskPatch`, after `target_conversation_id`:
```python
    end_at: datetime | None = None
```

In `ScheduledTaskOut`, after `last_fired_at`:
```python
    end_at: str | None
```

- [ ] **Step 2: Update `_to_out` to include `end_at`**

In `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py`, in `_to_out`:

```python
def _to_out(t: ScheduledTask) -> ScheduledTaskOut:
    return ScheduledTaskOut(
        id=t.id,
        name=t.name,
        status=t.status,
        schedule_kind=t.schedule_kind,
        cron_expr=t.cron_expr,
        interval_seconds=t.interval_seconds,
        run_at=_iso(t.run_at),
        timezone=t.timezone,
        prompt=t.prompt,
        target_mode=t.target_mode,
        target_conversation_id=t.target_conversation_id,
        owner_user_id=t.owner_user_id,
        next_fire_at=_iso(t.next_fire_at),
        last_fired_at=_iso(t.last_fired_at),
        end_at=_iso(t.end_at),          # ← add this line
        created_at=utc_isoformat(t.created_at),
        updated_at=utc_isoformat(t.updated_at),
    )
```

- [ ] **Step 3: Store `end_at` on create**

In `create_task`, after the `run_at=...` line, add:
```python
            end_at=_to_utc_naive(body.end_at) if body.end_at is not None else None,
```

- [ ] **Step 4: Handle `end_at` in PATCH (clearing + setting)**

In `patch_task`, after the `for field in (...)` loop block (right before `if touched_schedule:`), add:

```python
        # end_at must be handled separately: the generic loop skips None values,
        # so explicit null (clear-the-deadline) would be silently ignored.
        if "end_at" in body.model_fields_set:
            task.end_at = _to_utc_naive(body.end_at) if body.end_at is not None else None
```

- [ ] **Step 5: Confirm mypy passes**

```bash
cd backend && uv run mypy cubeplex/
```

Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/schemas/ws_scheduled_tasks.py \
        backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py
git commit -m "feat(scheduled-tasks): end_at field in API schemas + route (create/patch/out)"
```

---

### Task 4: Poller `end_at` enforcement

**Files:**
- Modify: `backend/cubeplex/repositories/scheduled_task.py`
- Modify: `backend/cubeplex/schedules/poller.py`

- [ ] **Step 1: Add `end_at` filter to `claim_due_tasks`**

In `backend/cubeplex/repositories/scheduled_task.py`, add `or_` to the imports:

```python
from sqlalchemy import or_, select, update
```

In `claim_due_tasks`, add the `or_()` condition inside `.where(...)`:

```python
    stmt = (
        select(ScheduledTask)
        .where(
            ScheduledTask.status == "active",  # type: ignore[arg-type]
            cast(Any, ScheduledTask.deleted_at).is_(None),
            cast(Any, ScheduledTask.next_fire_at).is_not(None),
            ScheduledTask.next_fire_at <= now,  # type: ignore[arg-type, operator]
            or_(
                cast(Any, ScheduledTask.end_at).is_(None),
                ScheduledTask.end_at > now,  # type: ignore[operator]
            ),
        )
        .order_by(ScheduledTask.next_fire_at)  # type: ignore[arg-type]
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
```

- [ ] **Step 2: Add expired guard in `_dispatch_one`**

In `backend/cubeplex/schedules/poller.py`, in `_dispatch_one`, after the existing `task is None` check (around line 229):

```python
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
```

- [ ] **Step 3: Confirm mypy passes**

```bash
cd backend && uv run mypy cubeplex/
```

Expected: `Success: no issues found`.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/repositories/scheduled_task.py \
        backend/cubeplex/schedules/poller.py
git commit -m "feat(scheduled-tasks): enforce end_at in poller claim and dispatch paths"
```

---

### Task 5: Backend E2E tests for `end_at` and 6-field rejection

**Files:**
- Modify: `backend/tests/e2e/test_scheduled_tasks_api.py`

- [ ] **Step 1: Add tests**

Inside the `TestScheduledTaskValidation` class in `test_scheduled_tasks_api.py`, add after the existing `test_invalid_cron_expr_422` test:

```python
    def test_6_field_cron_rejected_422(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", cron_expr="0 9 * * * *")
        assert r.status_code == 422
        assert "5 fields" in r.text

    def test_4_field_cron_rejected_422(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", cron_expr="9 * * *")
        assert r.status_code == 422
```

Add a new `TestScheduledTaskEndAt` class after the existing validation class:

```python
class TestScheduledTaskEndAt:
    def test_create_with_end_at_round_trips(self, client: TestClient) -> None:
        r = _make(client, end_at="2030-12-31T23:59:59+00:00")
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["end_at"] is not None
        assert "2030-12-31" in data["end_at"]

    def test_create_without_end_at_returns_null(self, client: TestClient) -> None:
        r = _make(client)
        assert r.status_code == 201, r.text
        assert r.json()["end_at"] is None

    def test_create_end_at_naive_datetime_rejected(self, client: TestClient) -> None:
        r = _make(client, end_at="2030-12-31T23:59:59")
        assert r.status_code == 422

    def test_patch_sets_end_at(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(
            f"{BASE}/{tid}",
            json={"end_at": "2030-06-01T00:00:00+00:00"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["end_at"] is not None

    def test_patch_clears_end_at_with_explicit_null(self, client: TestClient) -> None:
        tid = _make(client, end_at="2030-12-31T00:00:00+00:00").json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"end_at": None})
        assert r.status_code == 200, r.text
        assert r.json()["end_at"] is None
```

- [ ] **Step 2: Run the new E2E tests**

```bash
cd backend
uv run pytest tests/e2e/test_scheduled_tasks_api.py::TestScheduledTaskValidation::test_6_field_cron_rejected_422 \
              tests/e2e/test_scheduled_tasks_api.py::TestScheduledTaskValidation::test_4_field_cron_rejected_422 \
              tests/e2e/test_scheduled_tasks_api.py::TestScheduledTaskEndAt -v
```

Expected: 7 passed.

- [ ] **Step 3: Run the full scheduled-tasks E2E suite to check for regressions**

```bash
uv run pytest tests/e2e/test_scheduled_tasks_api.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_scheduled_tasks_api.py
git commit -m "test(scheduled-tasks): 6-field cron rejection + end_at round-trip E2E"
```

---

### Task 6: Core types — add `end_at`

**Files:**
- Modify: `frontend/packages/core/src/types/scheduled-task.ts`

- [ ] **Step 1: Add `end_at` to all relevant interfaces**

In `frontend/packages/core/src/types/scheduled-task.ts`:

```typescript
export interface ScheduledTaskOut {
  id: string
  name: string
  status: ScheduledTaskStatus
  schedule_kind: ScheduleKind
  cron_expr: string | null
  interval_seconds: number | null
  run_at: string | null
  timezone: string
  prompt: string
  target_mode: TargetMode
  target_conversation_id: string | null
  owner_user_id: string
  next_fire_at: string | null
  last_fired_at: string | null
  end_at: string | null       // ← add
  created_at: string
  updated_at: string
}

export interface ScheduledTaskCreate {
  name: string
  prompt: string
  schedule_kind: ScheduleKind
  cron_expr?: string
  interval_seconds?: number
  run_at?: string
  timezone?: string
  target_mode: TargetMode
  target_conversation_id?: string
  end_at?: string | null      // ← add; null = explicit "clear deadline" in PATCH
}

export type ScheduledTaskPatch = Partial<ScheduledTaskCreate>
```

- [ ] **Step 2: Build the core package**

```bash
cd frontend && pnpm --filter @cubeplex/core build
```

Expected: exits 0 with no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/types/scheduled-task.ts \
        frontend/packages/core/dist/
git commit -m "feat(core/types): add end_at to ScheduledTask interfaces"
```

---

### Task 7: `schedulePayload.ts` — types + build/parse utilities

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/lib/schedulePayload.ts`

- [ ] **Step 1: Create the file with types and `buildSchedulePayload`**

```typescript
// Pure, side-effect-free helpers that translate between the visual schedule
// editor's UI state and the API's schedule fields.

import type { ScheduledTaskCreate, ScheduledTaskOut } from '@cubeplex/core'

// ── UI state types ────────────────────────────────────────────────────────────

export interface DailySchedule {
  kind: 'daily'
  hour: number    // 0–23
  minute: number  // 0–59
}

export interface WeeklySchedule {
  kind: 'weekly'
  days: number[]  // croniter convention: 0=Sun 1=Mon … 6=Sat; at least one
  hour: number
  minute: number
}

export interface MonthlySchedule {
  kind: 'monthly'
  day: number | 'last'  // 1–28 or 'last' (croniter L)
  hour: number
  minute: number
}

export interface IntervalSchedule {
  kind: 'interval'
  value: number           // positive integer
  unit: 'minutes' | 'hours' | 'days'
}

export interface OnceSchedule {
  kind: 'once'
  runAt: string  // datetime-local value "YYYY-MM-DDTHH:mm"
}

export interface UnsupportedCronSchedule {
  kind: 'unsupported_cron'
  cronExpr: string
}

export type ScheduleState =
  | DailySchedule
  | WeeklySchedule
  | MonthlySchedule
  | IntervalSchedule
  | OnceSchedule
  | UnsupportedCronSchedule

export interface ScheduleEditorValue {
  schedule: ScheduleState
  timezone: string
  /** YYYY-MM-DD or null (null = no deadline / run forever) */
  endAt: string | null
}

// ── Default value for new tasks ───────────────────────────────────────────────

export function defaultScheduleEditorValue(tz: string): ScheduleEditorValue {
  return {
    schedule: { kind: 'daily', hour: 9, minute: 0 },
    timezone: tz,
    endAt: null,
  }
}

// ── Timezone-aware datetime helpers ─────────────────────────────────────────

/**
 * Convert a local datetime string ("YYYY-MM-DDTHH:mm") to a UTC ISO string,
 * interpreting the input as being in the given IANA timezone — NOT the browser's
 * local timezone. `new Date(str)` without a suffix uses the browser's TZ and
 * would produce the wrong UTC value whenever browser TZ ≠ task TZ.
 *
 * Algorithm: create a UTC "guess" treating the string as UTC, then measure how
 * far it is from the desired local time via Intl, and apply the delta.
 * One iteration is sufficient for standard offsets; DST edge cases (the
 * "spring forward" hour) may be off by 1h — acceptable for v1.
 */
export function localDatetimeToUTC(datetimeLocal: string, timezone: string): string {
  const guess = new Date(datetimeLocal + ':00Z')
  const tzRepr = guess.toLocaleString('sv', { timeZone: timezone }).replace(' ', 'T')
  const delta = guess.getTime() - new Date(tzRepr + 'Z').getTime()
  return new Date(guess.getTime() + delta).toISOString()
}

/**
 * Return the UTC instant at which the given date ends in the task timezone.
 * Defined as: start of the day AFTER `dateStr` in the task timezone.
 * Semantics: task fires on `dateStr` in local time, but not after.
 *
 * Example: dateStr="2026-06-01", tz="Asia/Shanghai" (UTC+8)
 *   → 2026-06-02T00:00:00+08:00 → 2026-06-01T16:00:00Z
 * A daily-09:00-CST task fires at 01:00Z; last fire Jun 1 (01:00Z < 16:00Z ✓),
 * first skip Jun 2 (01:00Z Jun 2 > 16:00Z Jun 1 ✓).
 */
export function endOfDayUTC(dateStr: string, timezone: string): string {
  const [y, mo, d] = dateStr.split('-').map(Number)
  const pad = (n: number) => String(n).padStart(2, '0')
  const nextDay = `${y}-${pad(mo)}-${pad(d + 1)}T00:00`
  return localDatetimeToUTC(nextDay, timezone)
}

// ── Build API payload from UI state ──────────────────────────────────────────

type SchedulePayload = Pick<
  ScheduledTaskCreate,
  'schedule_kind' | 'cron_expr' | 'interval_seconds' | 'run_at' | 'timezone' | 'end_at'
>

/**
 * Convert UI state to the backend schedule fields.
 * Cron times are kept in local time; the backend uses task.timezone for evaluation.
 * end_at: interpreted in the task timezone via endOfDayUTC (not UTC midnight).
 * Null (no deadline) is sent as explicit JSON null so PATCH can clear an existing deadline
 * via Pydantic's model_fields_set detection. The `once` kind omits end_at entirely.
 */
export function buildSchedulePayload(v: ScheduleEditorValue): SchedulePayload {
  const endAt: string | null = v.endAt ? endOfDayUTC(v.endAt, v.timezone) : null
  const s = v.schedule

  switch (s.kind) {
    case 'daily':
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} * * *`,
        timezone: v.timezone,
        end_at: endAt,
      }
    case 'weekly': {
      const dow = [...s.days].sort((a, b) => a - b).join(',')
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} * * ${dow}`,
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'monthly': {
      const dom = s.day === 'last' ? 'L' : String(s.day)
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} ${dom} * *`,
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'interval': {
      const unitSeconds: Record<IntervalSchedule['unit'], number> = {
        minutes: 60,
        hours: 3600,
        days: 86400,
      }
      return {
        schedule_kind: 'interval',
        interval_seconds: s.value * unitSeconds[s.unit],
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'once':
      return {
        schedule_kind: 'once',
        // Use localDatetimeToUTC, NOT new Date(s.runAt) — the latter uses the
        // browser's timezone, not the task timezone, producing wrong results
        // whenever the two differ.
        run_at: localDatetimeToUTC(s.runAt, v.timezone),
        timezone: v.timezone,
      }
    case 'unsupported_cron':
      return {
        schedule_kind: 'cron',
        cron_expr: s.cronExpr,
        timezone: v.timezone,
        end_at: endAt,
      }
  }
}

// ── Parse API task back to UI state (edit mode) ───────────────────────────────

/**
 * Parse a 5-field cron into a daily/weekly/monthly ScheduleState, or return
 * UnsupportedCronSchedule for any pattern we didn't generate ourselves.
 *
 * Pattern rules (all 5-field, fields: minute hour dom month dow):
 *   daily:   M H * * *
 *   weekly:  M H * * DOW   (DOW is not *)
 *   monthly: M H DOM * *   (DOM is not *)
 *   other:   unsupported_cron
 */
function parseCron(expr: string): ScheduleState {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return { kind: 'unsupported_cron', cronExpr: expr }

  const [rawMin, rawHour, dom, month, dow] = parts
  const minute = Number(rawMin)
  const hour = Number(rawHour)

  if (isNaN(minute) || isNaN(hour) || month !== '*') {
    return { kind: 'unsupported_cron', cronExpr: expr }
  }

  // daily: dom=* dow=*
  if (dom === '*' && dow === '*') {
    return { kind: 'daily', hour, minute }
  }

  // weekly: dom=* dow!=*
  if (dom === '*' && dow !== '*') {
    const days = dow.split(',').map(Number)
    if (days.some(isNaN)) return { kind: 'unsupported_cron', cronExpr: expr }
    return { kind: 'weekly', days, hour, minute }
  }

  // monthly: dom!=* dow=*
  if (dom !== '*' && dow === '*') {
    const day: number | 'last' = dom === 'L' ? 'last' : Number(dom)
    if (day !== 'last' && isNaN(day)) return { kind: 'unsupported_cron', cronExpr: expr }
    return { kind: 'monthly', day, hour, minute }
  }

  return { kind: 'unsupported_cron', cronExpr: expr }
}

function parseInterval(seconds: number): IntervalSchedule {
  if (seconds % 86400 === 0) return { kind: 'interval', value: seconds / 86400, unit: 'days' }
  if (seconds % 3600 === 0) return { kind: 'interval', value: seconds / 3600, unit: 'hours' }
  return { kind: 'interval', value: Math.round(seconds / 60), unit: 'minutes' }
}

export function parseSchedulePayload(task: ScheduledTaskOut): ScheduleEditorValue {
  // end_at was stored as "start of next day in task TZ" (via endOfDayUTC).
  // Reverse: convert to local date in task TZ, then subtract 1 day.
  let endAt: string | null = null
  if (task.end_at) {
    const nextDayLocal = new Date(task.end_at)
      .toLocaleString('sv', { timeZone: task.timezone })
      .slice(0, 10)  // "YYYY-MM-DD" of next day in task TZ
    const nextDay = new Date(nextDayLocal)
    nextDay.setDate(nextDay.getDate() - 1)
    endAt = nextDay.toISOString().slice(0, 10)  // back to the intended last day
  }

  let schedule: ScheduleState

  if (task.schedule_kind === 'cron' && task.cron_expr) {
    schedule = parseCron(task.cron_expr)
  } else if (task.schedule_kind === 'interval' && task.interval_seconds != null) {
    schedule = parseInterval(task.interval_seconds)
  } else if (task.schedule_kind === 'once' && task.run_at) {
    // Convert UTC ISO back to "YYYY-MM-DDTHH:mm" in the task's timezone.
    // task.run_at.slice(0,16) would give UTC wall-clock time, not local time.
    const localStr = new Date(task.run_at)
      .toLocaleString('sv', { timeZone: task.timezone })
      .replace(' ', 'T')
      .slice(0, 16)
    schedule = { kind: 'once', runAt: localStr }
  } else {
    schedule = { kind: 'unsupported_cron', cronExpr: task.cron_expr ?? '' }
  }

  return { schedule, timezone: task.timezone, endAt }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && pnpm --filter @cubeplex/web tsc --noEmit
```

Expected: no errors (new file, no imports from dialog yet).

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/scheduled-tasks/lib/schedulePayload.ts
git commit -m "feat(scheduled-tasks): schedulePayload types + build/parse utilities"
```

---

### Task 8: `ScheduleEditor` component

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduleEditor.tsx`

- [ ] **Step 1: Create the file**

```tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import type {
  DailySchedule,
  IntervalSchedule,
  MonthlySchedule,
  ScheduleEditorValue,
  ScheduleState,
  WeeklySchedule,
} from '../lib/schedulePayload'

// ── Frequency pills ──────────────────────────────────────────────────────────

type FreqKind = 'daily' | 'weekly' | 'monthly' | 'interval' | 'once'

const FREQ_LABELS: { kind: FreqKind; label: string }[] = [
  { kind: 'daily', label: '每天' },
  { kind: 'weekly', label: '每周' },
  { kind: 'monthly', label: '每月' },
  { kind: 'interval', label: '每隔…' },
  { kind: 'once', label: '一次' },
]

function FrequencyPills({
  value,
  onChange,
}: {
  value: FreqKind
  onChange: (kind: FreqKind) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {FREQ_LABELS.map(({ kind, label }) => (
        <button
          key={kind}
          type="button"
          onClick={() => onChange(kind)}
          className={cn(
            'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
            value === kind
              ? 'border-primary bg-primary/15 text-primary'
              : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground',
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Time input ───────────────────────────────────────────────────────────────

function TimeInput({
  hour,
  minute,
  onChange,
}: {
  hour: number
  minute: number
  onChange: (hour: number, minute: number) => void
}) {
  const value = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  return (
    <Input
      type="time"
      value={value}
      onChange={(e) => {
        const [h, m] = e.target.value.split(':').map(Number)
        if (!isNaN(h) && !isNaN(m)) onChange(h, m)
      }}
      className="max-w-[110px]"
    />
  )
}

// ── Timezone input ───────────────────────────────────────────────────────────

function TimezoneInput({ value, onChange }: { value: string; onChange: (tz: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [error, setError] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function validate(tz: string): boolean {
    try {
      Intl.DateTimeFormat(undefined, { timeZone: tz })
      return true
    } catch {
      return false
    }
  }

  function commit() {
    if (validate(draft)) {
      onChange(draft)
      setError(false)
      setEditing(false)
    } else {
      setError(true)
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => { setDraft(value); setEditing(true) }}
        className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
      >
        🌐 {value}
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1.5">
      <Input
        ref={inputRef}
        value={draft}
        onChange={(e) => { setDraft(e.target.value); setError(false) }}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
        className={cn('h-7 max-w-[200px] text-xs', error && 'border-destructive')}
        placeholder="Asia/Shanghai"
      />
      {error && <span className="text-xs text-destructive">无效时区</span>}
    </div>
  )
}

// ── End-date input ───────────────────────────────────────────────────────────

function EndDateInput({
  value,
  onChange,
}: {
  value: string | null
  onChange: (date: string | null) => void
}) {
  const enabled = value !== null
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          onClick={() => onChange(enabled ? null : new Date().toISOString().slice(0, 10))}
          className={cn(
            'relative h-4 w-7 rounded-full transition-colors',
            enabled ? 'bg-primary/40' : 'bg-border',
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 h-3 w-3 rounded-full transition-all',
              enabled ? 'left-3.5 bg-primary' : 'left-0.5 bg-muted-foreground',
            )}
          />
        </button>
        <Label className="text-xs font-normal text-muted-foreground">
          截止日期 <span className="text-primary text-[10px]">可选</span>
        </Label>
      </div>
      {enabled && (
        <div className="flex items-center gap-2">
          <Input
            type="date"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value || null)}
            className="max-w-[160px] text-xs"
            min={new Date().toISOString().slice(0, 10)}
          />
          <span className="text-xs text-muted-foreground">到期后自动停止</span>
        </div>
      )}
      {!enabled && (
        <p className="text-xs italic text-muted-foreground">未设置 — 永久运行</p>
      )}
    </div>
  )
}

// ── Weekday picker ───────────────────────────────────────────────────────────

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

function WeekdayPicker({
  value,
  onChange,
}: {
  value: number[]
  onChange: (days: number[]) => void
}) {
  function toggle(day: number) {
    const next = value.includes(day) ? value.filter((d) => d !== day) : [...value, day]
    if (next.length > 0) onChange(next)  // require at least one
  }
  return (
    <div className="flex gap-1">
      {WEEKDAYS.map((label, i) => (
        <button
          key={i}
          type="button"
          onClick={() => toggle(i)}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold transition-colors',
            value.includes(i)
              ? 'border-primary bg-primary/20 text-primary'
              : 'border-border text-muted-foreground hover:border-primary/40',
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Day-of-month picker ──────────────────────────────────────────────────────

function DayOfMonthPicker({
  value,
  onChange,
}: {
  value: number | 'last'
  onChange: (day: number | 'last') => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => onChange(d)}
            className={cn(
              'flex h-7 items-center justify-center rounded border text-[10px] font-semibold transition-colors',
              value === d
                ? 'border-primary bg-primary/20 text-primary'
                : 'border-border text-muted-foreground hover:border-primary/40',
            )}
          >
            {d}
          </button>
        ))}
        <button
          type="button"
          onClick={() => onChange('last')}
          className={cn(
            'col-span-3 flex items-center justify-center gap-1 rounded border py-1 text-[10px] font-semibold transition-colors',
            value === 'last'
              ? 'border-primary bg-primary/20 text-primary'
              : 'border-dashed border-primary/30 text-primary/60 hover:border-primary/50 hover:text-primary',
          )}
        >
          📅 月末最后一天
        </button>
        {/* fill remaining cells */}
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={`fill-${i}`} />
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground">
        1–28 避免短月 skip；月末选项自动适配每月实际天数
      </p>
    </div>
  )
}

// ── Interval input ───────────────────────────────────────────────────────────

function IntervalInput({
  value,
  onChange,
}: {
  value: IntervalSchedule
  onChange: (s: IntervalSchedule) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">每隔</span>
      <Input
        type="number"
        min={1}
        value={value.value}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10)
          if (n > 0) onChange({ ...value, value: n })
        }}
        className="w-16 text-center"
      />
      <select
        value={value.unit}
        onChange={(e) =>
          onChange({ ...value, unit: e.target.value as IntervalSchedule['unit'] })
        }
        className="rounded-md border border-border bg-input px-2 py-1.5 text-sm"
      >
        <option value="minutes">分钟</option>
        <option value="hours">小时</option>
        <option value="days">天</option>
      </select>
      <span className="text-sm text-muted-foreground">执行一次</span>
    </div>
  )
}

// ── ScheduleEditor (orchestrator) ────────────────────────────────────────────

interface ScheduleEditorProps {
  value: ScheduleEditorValue
  onChange: (value: ScheduleEditorValue) => void
}

function scheduleToFreqKind(s: ScheduleState): FreqKind {
  if (s.kind === 'unsupported_cron') return 'daily'  // won't be shown; see below
  return s.kind
}

function defaultForKind(kind: FreqKind, current: ScheduleState): ScheduleState {
  switch (kind) {
    case 'daily':
      return { kind: 'daily', hour: 9, minute: 0 }
    case 'weekly':
      return { kind: 'weekly', days: [1, 2, 3, 4, 5], hour: 9, minute: 0 }
    case 'monthly':
      return { kind: 'monthly', day: 1, hour: 9, minute: 0 }
    case 'interval':
      return { kind: 'interval', value: 1, unit: 'hours' }
    case 'once':
      return {
        kind: 'once',
        runAt: new Date(Date.now() + 86400_000).toISOString().slice(0, 16),
      }
  }
}

export function ScheduleEditor({ value, onChange }: ScheduleEditorProps) {
  const s = value.schedule
  const isLegacy = s.kind === 'unsupported_cron'

  function setSchedule(schedule: ScheduleState) {
    onChange({ ...value, schedule })
  }

  function handleFreqChange(kind: FreqKind) {
    setSchedule(defaultForKind(kind, s))
  }

  if (isLegacy) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
          ⚠ 当前使用了自定义 cron 表达式：
          <code className="ml-1 font-mono">{s.cronExpr}</code>
        </div>
        <button
          type="button"
          onClick={() => setSchedule({ kind: 'daily', hour: 9, minute: 0 })}
          className="self-start rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
        >
          切换到可视化配置（将清除当前 cron）
        </button>
      </div>
    )
  }

  const freqKind = scheduleToFreqKind(s)

  return (
    <div className="flex flex-col gap-3">
      {/* Frequency pills */}
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">频率</Label>
        <FrequencyPills value={freqKind} onChange={handleFreqChange} />
      </div>

      <hr className="border-border" />

      {/* Per-frequency sub-fields */}
      {(s.kind === 'daily' || s.kind === 'weekly' || s.kind === 'monthly') && (
        <>
          {s.kind === 'weekly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                运行日（可多选）
              </Label>
              <WeekdayPicker
                value={(s as WeeklySchedule).days}
                onChange={(days) => setSchedule({ ...(s as WeeklySchedule), days })}
              />
            </div>
          )}
          {s.kind === 'monthly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">日期</Label>
              <DayOfMonthPicker
                value={(s as MonthlySchedule).day}
                onChange={(day) => setSchedule({ ...(s as MonthlySchedule), day })}
              />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">运行时间</Label>
            <div className="flex items-center gap-2">
              <TimeInput
                hour={(s as DailySchedule).hour}
                minute={(s as DailySchedule).minute}
                onChange={(hour, minute) => setSchedule({ ...s, hour, minute } as ScheduleState)}
              />
              <TimezoneInput
                value={value.timezone}
                onChange={(tz) => onChange({ ...value, timezone: tz })}
              />
            </div>
          </div>
        </>
      )}

      {s.kind === 'interval' && (
        <IntervalInput
          value={s}
          onChange={(next) => setSchedule(next)}
        />
      )}

      {s.kind === 'once' && (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs uppercase tracking-wide text-muted-foreground">运行时间</Label>
          <div className="flex items-center gap-2">
            <Input
              type="datetime-local"
              value={s.runAt}
              onChange={(e) => setSchedule({ kind: 'once', runAt: e.target.value })}
              className="max-w-[220px]"
            />
            <TimezoneInput
              value={value.timezone}
              onChange={(tz) => onChange({ ...value, timezone: tz })}
            />
          </div>
        </div>
      )}

      {/* End date (not shown for once) */}
      {s.kind !== 'once' && (
        <EndDateInput
          value={value.endAt}
          onChange={(endAt) => onChange({ ...value, endAt })}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/web tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/scheduled-tasks/components/ScheduleEditor.tsx
git commit -m "feat(scheduled-tasks): ScheduleEditor component (frequency pills + sub-field UIs)"
```

---

### Task 9: Wire `ScheduleEditor` into `ScheduledTaskFormDialog`

**Files:**
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduledTaskFormDialog.tsx`

- [ ] **Step 1: Replace schedule-related state and fields**

Replace the full `ScheduledTaskFormDialog.tsx` with:

```tsx
'use client'

import { useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { createApiClient, createScheduledTask, patchScheduledTask } from '@cubeplex/core'
import type { ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskPatch } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ScheduleEditor } from './ScheduleEditor'
import {
  buildSchedulePayload,
  defaultScheduleEditorValue,
  parseSchedulePayload,
  type ScheduleEditorValue,
} from '../lib/schedulePayload'

interface ScheduledTaskFormDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  task: ScheduledTaskOut | null
  onSuccess: (task: ScheduledTaskOut) => void
}

function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}

export function ScheduledTaskFormDialog({
  wsId,
  open,
  onOpenChange,
  task,
  onSuccess,
}: ScheduledTaskFormDialogProps) {
  const isEdit = task !== null

  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [scheduleValue, setScheduleValue] = useState<ScheduleEditorValue>(
    defaultScheduleEditorValue(detectTimezone()),
  )
  const [targetMode, setTargetMode] = useState<'new_each_run' | 'fixed'>('new_each_run')
  const [targetConversationId, setTargetConversationId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form when dialog opens
  const [prevOpen, setPrevOpen] = useState(open)
  if (prevOpen !== open) {
    setPrevOpen(open)
    if (open) {
      if (task) {
        setName(task.name)
        setPrompt(task.prompt)
        setScheduleValue(parseSchedulePayload(task))
        setTargetMode(task.target_mode)
        setTargetConversationId(task.target_conversation_id ?? '')
      } else {
        setName('')
        setPrompt('')
        setScheduleValue(defaultScheduleEditorValue(detectTimezone()))
        setTargetMode('new_each_run')
        setTargetConversationId('')
      }
      setError(null)
    }
  }

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault()
    setSaving(true)
    setError(null)

    const client = createApiClient('')
    client.setWorkspaceId(wsId)

    const scheduleFields = buildSchedulePayload(scheduleValue)
    const body: ScheduledTaskCreate = {
      name: name.trim(),
      prompt: prompt.trim(),
      target_mode: targetMode,
      ...scheduleFields,
    }

    if (targetMode === 'fixed' && targetConversationId.trim()) {
      body.target_conversation_id = targetConversationId.trim()
    }

    try {
      let result: ScheduledTaskOut
      if (isEdit && task) {
        const patch: ScheduledTaskPatch = { ...body }
        result = await patchScheduledTask(client, task.id, patch)
      } else {
        result = await createScheduledTask(client, body)
      }
      onSuccess(result)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(580px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2 max-h-[90vh] overflow-y-auto',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="task-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {isEdit ? 'Edit scheduled task' : 'New scheduled task'}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <form onSubmit={(e) => void handleSubmit(e)} className="mt-4 flex flex-col gap-3">
            {/* Name */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-name">Name</Label>
              <Input
                id="task-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Daily digest"
                required
                maxLength={255}
              />
            </div>

            {/* Prompt */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-prompt">Prompt</Label>
              <Textarea
                id="task-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Summarize today's news and send me a digest…"
                required
                rows={3}
                className="resize-y"
              />
            </div>

            {/* Schedule */}
            <div className="flex flex-col gap-1.5">
              <Label>Schedule</Label>
              <ScheduleEditor value={scheduleValue} onChange={setScheduleValue} />
            </div>

            {/* Target mode */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-target-mode">Conversation target</Label>
              <Select
                value={targetMode}
                onValueChange={(v) => setTargetMode(v as typeof targetMode)}
              >
                <SelectTrigger id="task-target-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new_each_run">New conversation each run</SelectItem>
                  <SelectItem value="fixed">Fixed conversation</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {targetMode === 'fixed' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-conversation-id">Conversation ID</Label>
                <Input
                  id="task-conversation-id"
                  value={targetConversationId}
                  onChange={(e) => setTargetConversationId(e.target.value)}
                  placeholder="conv_…"
                  required={targetMode === 'fixed'}
                />
                <p className="text-xs text-muted-foreground">Must be one of your own conversations</p>
              </div>
            )}

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                {error}
              </div>
            )}

            <div className="mt-1 flex items-center justify-end gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" size="sm" disabled={saving}>
                    Cancel
                  </Button>
                }
              />
              <Button type="submit" size="sm" disabled={saving || !name.trim() || !prompt.trim()}>
                {saving ? (isEdit ? 'Saving…' : 'Creating…') : isEdit ? 'Save changes' : 'Create task'}
              </Button>
            </div>
          </form>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/web tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Run the dev server and smoke-test in browser**

```bash
cd frontend && pnpm dev
```

Open the scheduled-tasks page, click "New task", confirm:
- Frequency pills render
- Switching 每天/每周/每月/每隔/一次 shows the correct sub-fields
- Timezone badge shows the detected local timezone
- End date toggle works
- "每周" weekday pills are clickable
- "每月" day grid renders and selects correctly
- Submit creates a task (check Network tab for the API request body — should have 5-field `cron_expr`)

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/scheduled-tasks/components/ScheduledTaskFormDialog.tsx
git commit -m "feat(scheduled-tasks): replace raw cron/interval inputs with ScheduleEditor"
```

---

### Task 10: Playwright E2E smoke test update

**Files:**
- Modify: `frontend/packages/web/__tests__/e2e/scheduled-tasks.spec.ts`

- [ ] **Step 1: Add a new test covering the form UI flow**

At the end of `scheduled-tasks.spec.ts`, add:

```typescript
test('Scheduled Tasks: new-task dialog shows frequency pills, not cron input', async ({
  page,
}) => {
  const wsId = await registerAndLand(page)
  await page.goto(`/w/${wsId}/scheduled-tasks`)

  await page.getByRole('button', { name: /new task/i }).click()
  await expect(page.getByTestId('task-form-dialog')).toBeVisible()

  // Frequency pills visible
  await expect(page.getByRole('button', { name: '每天' })).toBeVisible()
  await expect(page.getByRole('button', { name: '每周' })).toBeVisible()
  await expect(page.getByRole('button', { name: '每月' })).toBeVisible()

  // No raw cron input
  await expect(page.locator('input[placeholder="0 9 * * 1-5"]')).not.toBeVisible()

  // Switch to 每周 and confirm weekday pills appear
  await page.getByRole('button', { name: '每周' }).click()
  await expect(page.getByRole('button', { name: '一' })).toBeVisible()

  // Switch to 每月 and confirm day grid appears
  await page.getByRole('button', { name: '每月' }).click()
  await expect(page.getByRole('button', { name: '15' })).toBeVisible()
  await expect(page.getByRole('button', { name: /月末/ })).toBeVisible()
})

test('Scheduled Tasks: create daily task via new UI → API receives 5-field cron', async ({
  page,
  request,
}) => {
  const wsId = await registerAndLand(page)
  const apiBase = await getApiBase(page)
  const { cookieHeader, csrf } = await getCookies(page)

  await page.goto(`/w/${wsId}/scheduled-tasks`)
  await page.getByRole('button', { name: /new task/i }).click()
  await page.getByLabel('Name').fill('Daily E2E')
  await page.getByLabel('Prompt').fill('Say hello')
  // Default is 每天 09:00 — just submit
  await page.getByRole('button', { name: /create task/i }).click()

  // Verify the created task has a 5-field cron
  const tasks = await request.get(`${apiBase}/api/v1/ws/${wsId}/scheduled-tasks`, {
    headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader },
  })
  const { tasks: list } = await tasks.json() as { tasks: Array<{ name: string; cron_expr: string | null }> }
  const created = list.find((t) => t.name === 'Daily E2E')
  expect(created).toBeDefined()
  expect(created!.cron_expr?.split(' ')).toHaveLength(5)
})
```

- [ ] **Step 2: Run the E2E tests**

```bash
cd frontend && npx playwright test __tests__/e2e/scheduled-tasks.spec.ts --reporter=list
```

Expected: all tests pass (including the two new ones).

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/scheduled-tasks.spec.ts
git commit -m "test(scheduled-tasks): Playwright smoke tests for frequency-pill form UI"
```

---

## Pre-PR checklist

- [ ] Run full backend suite: `cd backend && uv run pytest tests/unit/ tests/e2e/test_scheduled_tasks_api.py -v`
- [ ] Run full frontend type check: `cd frontend && pnpm --filter @cubeplex/web tsc --noEmit`
- [ ] Run Playwright suite: `cd frontend && npx playwright test`
- [ ] Open the form in dev, create one task of each frequency type, confirm network payloads are correct
- [ ] Confirm the production DB fix was applied: check `cron_expr` for `stask-1fTaVNGHVFa5CE`
