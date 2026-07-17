# Scheduled Task UX Redesign + Cron Validation Fix

**Date:** 2026-05-30  
**Scope:** Backend bug fix + frontend form redesign for non-developer UX

---

## Background

Two problems discovered from production run history:

1. **Bug:** `cron_expr = '0 9 * * * *'` (6 fields) was accepted by the validator.
   croniter treats the 6th field as seconds (`min hour dom month dow sec`), so
   `* * * * * *` = every second during minute 9 of hour 9 — 60 firings/day
   instead of 1. The task "每日 AI 日报" ran 9 times over 2 days (4–5×/day)
   instead of 2.

2. **UX:** The current form exposes raw cron syntax, IANA timezone strings, and
   interval-in-seconds — all opaque to non-developers.

---

## Part 1 — Bug Fix: Reject 6-Field Cron Expressions

### Backend

`_validate_cron` in `backend/cubeplex/api/schemas/ws_scheduled_tasks.py` currently
only calls `croniter.is_valid(expr)`, which accepts any field count.

Add a field-count check:

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

### Data fix

One existing record needs correcting:

```sql
UPDATE scheduled_tasks
SET cron_expr = '0 9 * * *'
WHERE id = 'stask-1fTaVNGHVFa5CE';
```

`next_fire_at` (`2026-05-31 01:00:00+00`) is already correct for the fixed
expression — no change needed there.

---

## Part 2 — Backend: `end_at` Field

### Model

Add to `ScheduledTask`:

```python
end_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```

Migration: `alembic revision --autogenerate -m "add end_at to scheduled_tasks"`.

### Poller

**`claim_due_tasks`** adds an `or_()` condition:

```python
or_(
    cast(Any, ScheduledTask.end_at).is_(None),
    ScheduledTask.end_at > now,  # type: ignore[operator]
)
```

**`claim_stale_runs` and `claim_busy_postponed_runs`** must also respect `end_at`.
These recovery sweeps re-dispatch stuck `claimed` run rows without consulting the
parent task. The simplest fix: in `_dispatch_one`, after loading the parent task,
add a deadline guard before dispatching:

```python
if task.end_at is not None and as_utc(task.end_at) <= now:
    row.state = "skipped_missed"
    row.detail = "task expired before dispatch"
    await session.commit()
    return
```

When a task's `end_at` is reached it simply stops being claimed — no state
change needed on the task row itself. The status stays `active`; the task just
never fires again. The UI can show "截止日期已过" when `end_at < now`.

### API schemas

`ScheduledTaskCreate`, `ScheduledTaskPatch`, and `ScheduledTaskOut` each get
`end_at: datetime | None` (nullable, no default enforcement). No extra
validation beyond timezone-awareness (same rule as `run_at`).

### PATCH clearing `end_at`

The existing PATCH route skips every `None` value (`if val is None: continue`),
which prevents clearing nullable fields. `end_at` must be exempted: apply it
when it appears in `body.model_fields_set`, even if the value is `None`:

```python
if "end_at" in body.model_fields_set:
    task.end_at = _to_utc_naive(body.end_at) if body.end_at is not None else None
```

Add this block inside the patch route, alongside the existing field loop.

---

## Part 3 — Frontend: Frequency-Based Form UI

Replaces the current `ScheduledTaskFormDialog` (schedule_kind dropdown +
raw cron/interval/once fields + manual IANA timezone input).

### Frequency options

A horizontal pill row replaces the schedule_kind `<Select>`:

| Pill label | Maps to backend |
|---|---|
| 每天 | `schedule_kind=cron`, `cron_expr="M H * * *"` |
| 每周 | `schedule_kind=cron`, `cron_expr="M H * * DOW"` |
| 每月 | `schedule_kind=cron`, `cron_expr="M H DOM * *"` or `"M H L * *"` |
| 每隔… | `schedule_kind=interval`, `interval_seconds=N×unit` |
| 一次 | `schedule_kind=once`, `run_at=ISO` |

Where `H:M` come from the time picker (converted to UTC via the selected
timezone before forming the cron), `DOW` = selected weekdays (0=Sunday … 6=Saturday,
comma-separated; croniter convention), `DOM` = 1–28 or `L` (last day, confirmed supported by croniter).

### Per-frequency fields

**每天**
- Time picker (`HH:mm`, 24h)
- Timezone (auto-detected, see below)
- End date (optional toggle + date input)

**每周**
- Weekday pills: 日一二三四五六, multi-select, at least one required
- Time picker
- Timezone
- End date (optional)

**每月**
- Day-of-month grid: cells 1–28 (single-select) + a "月末最后一天" cell
  spanning the remainder of the last row. Cells 29–31 are omitted entirely to
  avoid short-month skips. "月末最后一天" generates `L` in the DOM field
  (`croniter` supports it).
- Time picker
- Timezone
- End date (optional)

**每隔…**
- Number input (integer ≥ 1) + unit selector (分钟 / 小时 / 天)
- Minimum enforced: 1 minute (backend already requires `interval_seconds ≥ 60`)
- End date (optional)

**一次**
- Combined date-time input (`datetime-local`)
- Timezone
- No end date (task auto-completes after single run)

### Timezone UX

Replace the free-text IANA input with auto-detection:

1. On form open (new task): read `Intl.DateTimeFormat().resolvedOptions().timeZone`
   and pre-fill. Show badge `🌐 Asia/Shanghai（自动）`.
2. User clicks the badge → inline text input opens for manual override; badge
   changes to `✎ Asia/Shanghai（自定义）`.
3. On edit (existing task): pre-fill from `task.timezone`; show as custom.
4. Validation on blur: attempt `new Intl.DateTimeFormat(undefined, { timeZone: value })`
   — throws if invalid IANA name.

The timezone field is still sent to the backend unchanged (IANA string).

### UI → backend translation

A pure function `buildSchedulePayload(uiState) → ScheduledTaskCreate` handles
the mapping. Key points:

- `HH:mm` from the time picker is used **directly as local time** in the cron
  expression (e.g. `0 9` for 09:00). The backend's `compute.py` evaluates the
  cron in the task's `timezone` field, so no UTC conversion is needed in the
  frontend.
- `end_at` is sent as a UTC ISO string when the toggle is on; omitted (or
  `null` on PATCH) when off.

### Backend → UI reverse translation (edit mode)

A companion function `parseSchedulePayload(task: ScheduledTaskOut) → UIState`
reconstructs the form state for editing. Because we generate the cron
ourselves, the pattern is deterministic:

- 5 fields, all `*` except time + one dimension = unambiguous
- `*/...` not used (rejected by the 5-field validator anyway)
- `L` in DOM field → "月末最后一天"
- Multiple values in DOW field → multi-select weekday pills
- `schedule_kind=interval` → "每隔…" + reverse-divide `interval_seconds` to
  the largest clean unit (86400→天, 3600→小时, else →分钟)

**Legacy cron fallback:** Tasks created via the old raw-input form (e.g.
`0 9 1,15 * *`, `*/2 * * * *`) may not map to any of the five frequency UI
patterns. `parseSchedulePayload` must detect this and return a special
`{ kind: "unsupported_cron", cron_expr }` state. When the form renders in edit
mode with this state:

- Show a read-only badge: `⚠ 当前使用了自定义 cron 表达式: 0 9 1,15 * *`
- The frequency pills and sub-fields are hidden
- The user can click "切换到可视化配置" to explicitly convert — at which point
  the form resets to "每天" defaults and the old cron is discarded (warn before
  clearing)
- Saving without clicking the convert button preserves the original cron_expr
  unchanged

This prevents silent schedule corruption when editing existing tasks.

### Component structure

```
ScheduledTaskFormDialog          (dialog shell, submit handler — existing file)
  └── ScheduleEditor             (new component: frequency + all sub-fields)
        ├── FrequencyPills
        ├── TimeInput
        ├── TimezoneInput        (auto-detect logic lives here)
        ├── WeekdayPicker        (used by 每周)
        ├── DayOfMonthPicker     (used by 每月)
        ├── IntervalInput        (used by 每隔…)
        ├── DatetimeOnceInput    (used by 一次)
        └── EndDateInput         (used by 每天/每周/每月/每隔…)
```

All sub-components are local to the `scheduled-tasks/components/` directory.

---

## What Is Not Changing

- Backend `schedule_kind` enum (`cron / interval / once`) — frontend
  translates into these, API surface unchanged except `end_at`.
- Poller logic, dispatch, completion hook — untouched.
- Existing `ScheduledTaskCard`, `ScheduledTasksList`, `ScheduledTaskRunsPanel`
  — display side is out of scope.
- No support for 6-field / sub-minute cron going forward (explicitly rejected).

---

## Testing

**Backend unit tests** (`tests/unit/test_schedule_compute.py` or new
`test_ws_scheduled_tasks_schemas.py`):
- `_validate_cron` rejects 6-field expressions
- `_validate_cron` rejects 4-field expressions
- `end_at` in the past does not cause a validation error (it's allowed to
  create a task with a past end_at; the poller simply never fires it)

**E2E tests** (`tests/e2e/test_scheduled_tasks_api.py`):
- POST with 6-field cron returns 422
- POST with `end_at` in the future → task created, `end_at` round-trips
- Existing create/edit/delete flows continue to pass

**Frontend E2E** (`frontend/packages/web/__tests__/e2e/scheduled-tasks.spec.ts`):
- Create 每天 task → verify cron_expr is 5-field in network request
- Create 每周 task (Mon+Wed) → verify DOW field
- Create 每月 task (last day) → verify `L` in cron
- Create 每隔… task (2 小时) → verify `interval_seconds=7200`
- Edit existing task → verify form pre-fills correctly
- End date toggle on → `end_at` present in payload; off → absent
