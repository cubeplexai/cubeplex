# UTC Timestamps Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Spec: `docs/dev/specs/2026-05-28-utc-timestamps-design.md` (HEAD `afe460e6`). Stay on branch `feat/utc-timestamps` (slot 85, port 8085). Read `.worktree.env` first inside the worktree. Never `--no-verify`, never amend, never switch branches mid-execution. Each Task ends with a commit.

**Goal:** Migrate all 22 `datetime` columns in cubeplex from naïve Postgres `timestamp without time zone` to tz-aware `timestamptz`. After the migration, DB stores absolute UTC instants regardless of session `TimeZone`, and Python-side defensive `replace(tzinfo=UTC)` blocks are deleted.

**Architecture:** Single PR. (1) Convert all 22 fields to `sa_column=Column(DateTime(timezone=True), ...)`. (2) Autogen one alembic revision, hand-add `postgresql_using="<col> AT TIME ZONE 'UTC'"` to each `alter_column` (autogen omits it; default cast applies session `TimeZone` — wrong for our stored UTC values). (3) Delete five Python-side defensive blocks rendered dead by the migration. (4) Tighten `utc_isoformat` to assert tz-aware. (5) Fix two test files that explicitly seeded naïve datetimes. (6) Add a CLAUDE.md hard rule.

**Tech Stack:** SQLModel + SQLAlchemy + Alembic (autogen), Postgres `timestamptz`, pytest, ruff + mypy strict.

---

## Task 0 — Pre-flight

**Files:** none (sanity only).

- [ ] **Step 1: Verify worktree, branch, and slot**

```bash
cd /home/chris/cubeplex/.worktrees/feat/utc-timestamps
cat .worktree.env
git rev-parse --abbrev-ref HEAD
```

Expected: `feat/utc-timestamps`. Slot `85`, ports `8085`/`3085`, DB `cubeplex_feat_utc_timestamps`. If not on that branch, STOP and report.

- [ ] **Step 2: Run the baseline test sweep on the unchanged tree**

```bash
cd backend && uv run pytest -q
cd backend && uv run mypy cubeplex/
```

Expected: all green. Record the count (we want the same count green after migration). This is the baseline regression net.

- [ ] **Step 3: Bring the worktree DB to head**

```bash
cd backend && uv run alembic upgrade head
```

Expected: "Running upgrade ... (no migration to run)" or terminates successfully. If the DB is fresh, this creates all tables in the current naïve form. We need this baseline so the upcoming autogen detects only our intended diff.

- [ ] **Step 4: Confirm the spec is at the expected commit**

```bash
git log --oneline -1 docs/dev/specs/2026-05-28-utc-timestamps-design.md
```

Expected: includes `afe460e6` (latest spec revision). If not, STOP — the plan may be stale relative to the spec.

No commit in this task. It's verification only.

---

## Task 1 — Convert 11 model files to tz-aware columns

**Files:**
- Modify: `backend/cubeplex/models/mixins.py` (CubeplexBase/TimestampMixin — 2 cols, cascades to most tables)
- Modify: `backend/cubeplex/models/user_sandbox.py` (5 cols)
- Modify: `backend/cubeplex/models/provider.py` (2 cols)
- Modify: `backend/cubeplex/models/skill.py` (3 cols)
- Modify: `backend/cubeplex/models/invite_token.py` (2 cols)
- Modify: `backend/cubeplex/models/billing.py` (2 cols)
- Modify: `backend/cubeplex/models/mcp.py` (2 cols)
- Modify: `backend/cubeplex/models/conversation.py` (1 col)
- Modify: `backend/cubeplex/models/egress_ref.py` (1 col)
- Modify: `backend/cubeplex/models/memory.py` (1 col)
- Modify: `backend/cubeplex/models/attachment.py` (1 col)

All 22 fields must be converted in one task because the next task (autogen) needs to see every change at once to produce a single complete revision.

Conversion patterns (apply mechanically):

**Pattern A — non-null with default_factory (e.g. `created_at`, `installed_at`):**

```python
# before
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

# after
created_at: datetime = Field(
    default_factory=lambda: datetime.now(UTC),
    sa_column=Column(DateTime(timezone=True), nullable=False),
)
```

**Pattern B — nullable, no factory (e.g. `paused_at`, `deleted_at`):**

```python
# before
paused_at: datetime | None = Field(default=None)

# after
paused_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```

**Pattern C — nullable + indexed (e.g. `in_use_until`, `last_provider_check`):**

```python
# before
in_use_until: datetime | None = Field(default=None, index=True)

# after
in_use_until: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
)
```

**Pattern D — bare annotation (e.g. `attached_at`, `last_discovered_at`, `expires_at` in mcp.py):**

```python
# before
attached_at: datetime | None = None

# after
attached_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```

`__table_args__ = (Index(...))` in `invite_token.py` and the per-model table-level Index in `conversation.py` STAY AS THEY ARE — they reference the column by name and survive the type change. Don't touch them.

For each model file:
1. Add `from sqlalchemy import Column, DateTime` if not already imported.
2. Apply the matching pattern per column listed below.

Steps:

- [ ] **Step 1: Convert `mixins.py` (`TimestampMixin.created_at`, `updated_at` — pattern A)**

Edit `backend/cubeplex/models/mixins.py`:

```python
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel

from cubeplex.models.public_id import generate_public_id


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns.

    Use directly on tables with composite/non-prefixed PKs (e.g. association
    tables). Tables with a synthetic public-id PK get these for free via
    :class:`CubeplexBase`.
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
```

The rest of the file (`CubeplexBase`, `OrgScopedMixin`, `org_scope_index`) is unchanged.

- [ ] **Step 2: Convert `user_sandbox.py` (5 cols)**

Add `from sqlalchemy import Column, DateTime` to the imports (the file already imports `Column, Index` from `sqlalchemy`; just add `DateTime`).

Apply:
- `last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` → pattern A.
- `paused_at: datetime | None = Field(default=None)` → pattern B.
- `paused_ttl_seconds: int = Field(...)` — NOT a datetime, leave alone.
- `last_resumed_at: datetime | None = Field(default=None)` → pattern B.
- `in_use_until: datetime | None = Field(default=None, index=True)` → pattern C.
- `last_provider_check: datetime | None = Field(default=None, index=True)` → pattern C.

- [ ] **Step 3: Convert `provider.py` (2 cols)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `last_liveness_at: datetime | None = Field(default=None)` (line 57) → pattern B.
- `last_test_at: datetime | None = Field(default=None)` (line 93) → pattern B.

- [ ] **Step 4: Convert `skill.py` (3 cols)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `deprecated_at: datetime | None = Field(default=None, nullable=True)` (line 31) → pattern B (the existing `nullable=True` kwarg moves into `Column(...)`).
- `installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` (line 74) → pattern A.
- `hidden_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` (line 120) → pattern A.

- [ ] **Step 5: Convert `invite_token.py` (2 cols)**

Add `from sqlalchemy import Column, DateTime` import (the file already imports `Index` from `sqlalchemy`).

Apply:
- `expires_at: datetime = Field(default_factory=_default_expiry)` (line 22) → pattern A (keeps the existing `_default_expiry` factory; the function itself already returns tz-aware so no body change).
- `used_at: datetime | None = Field(default=None)` (line 23) → pattern B.

The `__table_args__ = (Index("ix_invite_tokens_expires", "expires_at"),)` line is unchanged.

- [ ] **Step 6: Convert `billing.py` (2 cols)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` (line 28) → pattern A.
- `ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` (line 29) → pattern A.

- [ ] **Step 7: Convert `mcp.py` (2 cols, both pattern D)**

Add `from sqlalchemy import Column, DateTime` (it likely already imports `Column` for the JSON columns; check).

Apply:
- `last_discovered_at: datetime | None = None` (line 187) → pattern D.
- `expires_at: datetime | None = None` (line 285) → pattern D.

- [ ] **Step 8: Convert `conversation.py` (1 col)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `deleted_at: datetime | None = Field(default=None)` (line 42) → pattern B.

- [ ] **Step 9: Convert `egress_ref.py` (1 col)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `expires_at: datetime | None = Field(default=None, nullable=True)` (line 29) → pattern B (the existing `nullable=True` moves into `Column(...)`).

- [ ] **Step 10: Convert `memory.py` (1 col)**

Add `from sqlalchemy import Column, DateTime` import.

Apply:
- `last_used_at: datetime | None = Field(default=None)` (line 76) → pattern B.

- [ ] **Step 11: Convert `attachment.py` (1 col, pattern D)**

Apply:
- `attached_at: datetime | None = None` (line 44) → pattern D. Need `from datetime import datetime` (already there) AND `from sqlalchemy import Column, DateTime` (new import) AND `from sqlmodel import Field` (check whether the file imports `Field`; if not, add it).

- [ ] **Step 12: Sanity-check mypy on the changed files**

```bash
cd backend && uv run mypy cubeplex/models/
```

Expected: no new mypy errors. If `Column(...)` constructor signature complaints surface, add `# type: ignore[arg-type]` only where mypy strictly demands; don't blanket-ignore.

- [ ] **Step 13: Commit Task 1**

```bash
git add backend/cubeplex/models/
git commit -m "refactor(models): tz-aware datetime columns (timestamptz) (#???)"
```

The commit replaces `???` with the PR number once known; for now use a placeholder. The whole point of this commit is "model schema change; migration in next commit". Pre-commit ruff + mypy must pass.

---

## Task 2 — Autogen migration + hand-edit `postgresql_using`

**Files:**
- Create (autogen): `backend/alembic/versions/<rev>_utc_timestamps.py`

This task has multiple commits: one for the raw autogen output, one for the hand-edit. Reviewers can see exactly which lines the human touched.

Steps:

- [ ] **Step 1: Run autogen**

```bash
cd backend && uv run alembic revision --autogenerate -m "utc timestamps"
```

Expected: `Generated .../versions/<rev>_utc_timestamps.py` with 22 `op.alter_column` calls switching `type_=sa.DateTime(timezone=True), existing_type=sa.DateTime()` per column. No table renames, no unrelated drift. If autogen produces unrelated drift, STOP — the model changes in Task 1 likely introduced an unintended diff; fix the model, not the migration.

- [ ] **Step 2: Commit the raw autogen output**

```bash
git add backend/alembic/versions/
git commit -m "feat(migration): autogen utc timestamps (raw, pre-postgresql_using)"
```

The point of this commit is reproducibility — anyone re-running autogen on the same code should produce this exact file. The hand-edit follows in Step 4.

- [ ] **Step 3: Hand-add `postgresql_using="<col> AT TIME ZONE 'UTC'"` to every `alter_column` call**

For each of the 22 `op.alter_column(...)` calls in `upgrade()` AND `downgrade()`, add the `postgresql_using` kwarg. Example, before:

```python
op.alter_column(
    "user_sandboxes",
    "last_activity_at",
    type_=sa.DateTime(timezone=True),
    existing_type=sa.DateTime(),
    existing_nullable=False,
)
```

After:

```python
op.alter_column(
    "user_sandboxes",
    "last_activity_at",
    type_=sa.DateTime(timezone=True),
    existing_type=sa.DateTime(),
    existing_nullable=False,
    postgresql_using="last_activity_at AT TIME ZONE 'UTC'",
)
```

The `downgrade()` body has the mirror `alter_column` calls; add the same `postgresql_using="<col> AT TIME ZONE 'UTC'"` form (because `<timestamptz> AT TIME ZONE 'UTC'` returns a naïve `timestamp without time zone` carrying the UTC clock value — exactly the original pre-migration state).

Also add a docstring at the top of the revision body explaining why the hand-edit exists:

```python
"""utc timestamps

Revision ID: <rev>
Revises: <prev>
Create Date: <date>

This migration switches all 22 cubeplex datetime columns from
``timestamp without time zone`` to ``timestamp with time zone``.

Hand-edit: every ``op.alter_column`` call carries a manually-added
``postgresql_using="<col> AT TIME ZONE 'UTC'"`` parameter. Autogen omits
it, which would default Postgres to ``USING column::timestamptz`` — that
interprets the naïve clock value as session local time, drifting all
stored timestamps by the session ``TimeZone`` offset. Our stored values
are UTC clocks, so ``AT TIME ZONE 'UTC'`` is the correct cast.

See ``docs/dev/specs/2026-05-28-utc-timestamps-design.md`` §3.
"""
```

- [ ] **Step 4: Commit the hand-edit**

```bash
git add backend/alembic/versions/<rev>_utc_timestamps.py
git commit -m "feat(migration): hand-add postgresql_using='AT TIME ZONE \\'UTC\\'' on 22 alter_columns"
```

- [ ] **Step 5: Verify migration round-trip**

```bash
cd backend && uv run alembic upgrade head
# Expected: "Running upgrade ... -> <rev>, utc timestamps" with no errors.

cd backend && uv run alembic downgrade -1
# Expected: "Running downgrade <rev> -> <prev>, utc timestamps" with no errors.

cd backend && uv run alembic upgrade head
# Expected: same upgrade as before, clean.
```

If any of the three steps errors, STOP and investigate. Common failure: a `postgresql_using` typo (single quote escaping) or a column rename that snuck into autogen output.

No commit at this step — verification only.

---

## Task 3 — Delete 4 naïve-fallback defense blocks

**Files:**
- Modify: `backend/cubeplex/repositories/invite_token.py:30-32`
- Modify: `backend/cubeplex/repositories/egress_ref.py:33-34`
- Modify: `backend/cubeplex/mcp/effective.py:562-563`
- Modify: `backend/cubeplex/mcp/oauth/token_manager.py:339-340`

These blocks all look like:

```python
if x.tzinfo is None:
    x = x.replace(tzinfo=UTC)
```

They guard DB-read datetimes that were previously naïve. After Task 2, the DB returns tz-aware values; these branches are dead. Delete them outright.

Steps:

- [ ] **Step 1: Delete the block in `repositories/invite_token.py`**

Read the function around lines 28-38, identify the block:

```python
expires_at = tok.expires_at
if expires_at.tzinfo is None:
    expires_at = expires_at.replace(tzinfo=UTC)
if tok.used_at is not None or expires_at < now:
```

becomes:

```python
if tok.used_at is not None or tok.expires_at < now:
```

(`tok.expires_at` is the DB-read value — used directly, no intermediate variable needed once the defense is gone.)

If `expires_at` is referenced elsewhere in the function, keep the local binding but drop the `if .tzinfo` block.

- [ ] **Step 2: Delete the block in `repositories/egress_ref.py:33-34`**

The defensive block sits inside whatever method reads `EgressRef.expires_at`. Same pattern: drop the `if exp.tzinfo is None: exp = exp.replace(tzinfo=UTC)` pair.

- [ ] **Step 3: Delete the block in `mcp/effective.py:562-563`**

Same pattern: drop `if when.tzinfo is None: when = when.replace(tzinfo=UTC)`.

- [ ] **Step 4: Delete the block in `mcp/oauth/token_manager.py:339-340`**

Same pattern: drop `if expires_at.tzinfo is None: expires_at = expires_at.replace(tzinfo=UTC)`.

- [ ] **Step 5: mypy + targeted test run**

```bash
cd backend && uv run mypy cubeplex/repositories/invite_token.py cubeplex/repositories/egress_ref.py cubeplex/mcp/effective.py cubeplex/mcp/oauth/token_manager.py
cd backend && uv run pytest tests/ -k "invite_token or egress_ref or mcp_effective or token_manager" -q
```

Expected: green. If a test fails because a fixture seeded naïve datetimes (e.g., `test_egress_exchange_service.py`), Task 5 fixes it — for now just record which tests failed and move on. If a NEW failure surfaces that doesn't match Task 5's known list, STOP and investigate.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/repositories/invite_token.py backend/cubeplex/repositories/egress_ref.py backend/cubeplex/mcp/effective.py backend/cubeplex/mcp/oauth/token_manager.py
git commit -m "refactor(timestamps): drop naive-fallback tzinfo defenses (DB now returns tz-aware)"
```

---

## Task 4 — Tighten `utc_isoformat` to assert tz-aware

**Files:**
- Modify: `backend/cubeplex/utils/time.py`

Steps:

- [ ] **Step 1: Replace the function body**

```python
"""Timezone-safe datetime utilities."""

from datetime import datetime


def utc_isoformat(dt: datetime) -> str:
    """Return an ISO 8601 string with the UTC offset.

    Post-timestamptz-migration, every datetime in cubeplex is tz-aware by
    construction. A naïve dt reaching this helper means someone violated
    the CLAUDE.md "tz-aware time columns" hard rule — fail loudly so the
    bug is visible rather than silently fixed.
    """
    assert dt.tzinfo is not None, (
        f"naive datetime reached utc_isoformat: {dt!r}; should be tz-aware"
    )
    return dt.isoformat()
```

Note the import line dropped `UTC` (no longer used). If mypy complains about `UTC` removal elsewhere because other names from this file are imported, double-check; only this module's own imports change.

- [ ] **Step 2: Run targeted tests**

```bash
cd backend && uv run pytest tests/unit/ -k "utc_isoformat or time" -q
```

Expected: no test relies on the naïve-fallback behaviour. If there's a test that explicitly hands `utc_isoformat` a naïve datetime, that test is now incorrect — convert it to pass tz-aware (`datetime.now(UTC)`) instead. Report which test if so.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/utils/time.py
git commit -m "refactor(timestamps): utc_isoformat asserts tz-aware (loud failure on regressions)"
```

---

## Task 5 — Fix the 2 test files using naïve datetimes

**Files:**
- Modify: `backend/tests/e2e/test_sandbox_lease.py:118, 155` (and any local `_as_naive_utc` helper)
- Modify: `backend/tests/unit/test_egress_exchange_service.py:111, 121, 194, 202`

Steps:

- [ ] **Step 1: Fix `test_sandbox_lease.py`**

Read the file around lines 115-160. Two places call `datetime.utcnow()`:

```python
assert in_use > datetime.utcnow()
# ...
before = datetime.utcnow()
```

Replace with:

```python
assert in_use > datetime.now(UTC)
# ...
before = datetime.now(UTC)
```

If the file has a `_as_naive_utc` helper (sometimes named `_naive_utc`, `_strip_tz`, etc.) that was added during PR #156 to work around naïve-column behaviour, delete it AND remove its call sites — comparisons now work directly between tz-aware values.

Verify imports: `from datetime import UTC, datetime, timedelta` should already be present.

- [ ] **Step 2: Fix `test_egress_exchange_service.py`**

Read the file around lines 105-210. Identify the fixture that seeds `expires_at` as naïve:

```python
# Store expires_at as tz-naive to simulate what Postgres DateTime() returns
naive_expires_at = expires_at.replace(tzinfo=None)
# ...
expires_at=naive_expires_at,
```

This whole "simulate naïve" pretence is now wrong — Postgres returns tz-aware. Delete the `.replace(tzinfo=None)` step and pass the original tz-aware `expires_at` straight into the seeded row:

```python
expires_at=expires_at,  # tz-aware as the column now stores
```

If the test docstrings reference "tz-naïve from DB", update them to "tz-aware from DB".

- [ ] **Step 3: Run the affected test files**

```bash
cd backend && uv run pytest tests/e2e/test_sandbox_lease.py tests/unit/test_egress_exchange_service.py -q
```

Expected: all green. If a test now fails because it was actually depending on the broken naïve behaviour (e.g., expected a `TypeError` from comparing naïve with aware), that's a real bug the migration fixed — adjust the assertion.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_sandbox_lease.py backend/tests/unit/test_egress_exchange_service.py
git commit -m "test(timestamps): drop naive-datetime simulation in lease + egress tests"
```

---

## Task 6 — Audit & remove remaining defensive tzinfo handling

**Files:** depends on grep results. Known one: `backend/cubeplex/sandbox/manager.py` (the G11 grace block introduced by PR #156).

Steps:

- [ ] **Step 1: Grep for remaining defensive `tzinfo is None` blocks across production code**

```bash
grep -rn "tzinfo is None\|replace(tzinfo=" backend/cubeplex/ --include="*.py"
```

Expected output: only matches in `utils/time.py` (the assert we just wrote — `tzinfo is not None`, not `is None`; the grep may match incidentally — read context to confirm). All other matches should already be deleted by Tasks 3 + 4.

Known remaining hit: `backend/cubeplex/sandbox/manager.py` G11 grace block (around the `pause_idle_age = (datetime.now(UTC) - last_activity).total_seconds()` section, lines ~920-940 — look for `if last_activity.tzinfo is None`).

- [ ] **Step 2: Delete `manager.py` G11 defensive block**

Find the block:

```python
# ``last_activity_at`` is stored TZ-naive (column has no
# ``timezone=True``), so normalise both sides before the
# subtraction or it raises TypeError.
last_activity = record.last_activity_at
if last_activity.tzinfo is None:
    last_activity = last_activity.replace(tzinfo=UTC)
pause_idle_age = (datetime.now(UTC) - last_activity).total_seconds()
```

Replace with:

```python
pause_idle_age = (datetime.now(UTC) - record.last_activity_at).total_seconds()
```

The comment about TZ-naïve storage is now wrong; delete it together with the defensive code.

- [ ] **Step 3: Re-grep**

```bash
grep -rn "tzinfo is None\|replace(tzinfo=" backend/cubeplex/ --include="*.py"
```

Expected output: only `utc_isoformat`'s assert line (which contains `tzinfo is not None`, technically matched by the grep). All real defensive blocks should be gone.

- [ ] **Step 4: Run the manager test sweep**

```bash
cd backend && uv run pytest tests/unit/test_sandbox_manager_pause.py tests/unit/test_sandbox_reconciler.py tests/e2e/test_sandbox_pause_concurrency.py -q
```

Expected: green. The reconciler grace test (`test_reconcile_pausing_stuck_past_grace_kills_instead_of_reverting`) directly exercises the simplified G11 grace path.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/sandbox/manager.py
git commit -m "refactor(sandbox): drop G11 grace tzinfo defense (timestamptz makes it redundant)"
```

---

## Task 7 — Add CLAUDE.md hard rule

**Files:**
- Modify: `CLAUDE.md` (under `## Hard Code Rules`, anywhere in the bullet list)

Steps:

- [ ] **Step 1: Insert the new rule**

Read `CLAUDE.md` and find the `## Hard Code Rules` section (around line 60). Add a new bullet between existing rules (a natural location is right after the `Datetimes from DB → utc_isoformat()` line):

```markdown
- **Time columns are tz-aware.** All `datetime` model fields use
  `sa_column=Column(DateTime(timezone=True), ...)` (Postgres `timestamptz`).
  Application code writes `datetime.now(UTC)` (tz-aware). Frontend gets
  ISO 8601 with `+00:00` (via `utc_isoformat()`) or `Z` (via Pydantic
  default) — both valid. No naïve `datetime` ever crosses the DB or
  service-API boundary. When introducing a new datetime column or
  converting an existing one, the alembic migration must hand-add
  `postgresql_using="<col> AT TIME ZONE 'UTC'"` on each `alter_column`
  call — autogen omits it, and the default cast applies the session
  `TimeZone` (wrong for our stored UTC values).
```

If the existing `Datetimes from DB → utc_isoformat()` bullet feels redundant after this insert, leave it as a narrower restatement.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): hard rule on tz-aware datetime columns"
```

---

## Task 8 — Pre-PR sweep + manual TZ smoke check

**Files:** none (verification + smoke).

Steps:

- [ ] **Step 1: Full backend test suite**

```bash
cd backend && uv run pytest -q
```

Expected: same number of `passed` as Task 0 baseline. Any regression is a Task 1-7 mistake — fix it in the relevant earlier task's spirit, do not bypass.

- [ ] **Step 2: mypy strict full sweep**

```bash
cd backend && uv run mypy cubeplex/
```

Expected: clean.

- [ ] **Step 3: Migration final round-trip**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run alembic downgrade -1
cd backend && uv run alembic upgrade head
```

Expected: each step clean.

- [ ] **Step 4: Manual Postgres `TimeZone=Shanghai` smoke check**

This is a one-off manual verification that the migration achieved the goal (TZ-aware columns return absolute UTC moments regardless of session TimeZone). It is NOT automated.

```bash
# Get the slot 85 test DB connection string from .worktree.env (it's
# cubeplex_test_feat_utc_timestamps). Or use the dev DB for the worktree.
psql "host=localhost dbname=cubeplex_feat_utc_timestamps" <<'SQL'
-- Seed one row using the application's would-be write path (UTC-now).
INSERT INTO user_sandboxes (id, user_id, sandbox_id, image, status,
    ttl_seconds, last_activity_at, paused_at, paused_ttl_seconds,
    created_at, updated_at, org_id, workspace_id, provider)
VALUES ('sbx-tz-smoke', 'usr-1', 'sbx-1', 'img', 'paused', 0,
    NOW(), NOW(), 60, NOW(), NOW(), 'org-1', 'ws-1', 'opensandbox');

SET TIME ZONE 'Asia/Shanghai';
SELECT paused_at, paused_at + INTERVAL '1 minute' <= NOW() FROM user_sandboxes
WHERE id = 'sbx-tz-smoke';

SET TIME ZONE 'UTC';
SELECT paused_at, paused_at + INTERVAL '1 minute' <= NOW() FROM user_sandboxes
WHERE id = 'sbx-tz-smoke';

-- Clean up.
DELETE FROM user_sandboxes WHERE id = 'sbx-tz-smoke';
SQL
```

Expected:
- Under `SET TIME ZONE 'Asia/Shanghai'` the `paused_at` value renders with `+08` offset BUT the arithmetic result (whether `paused_at + 1 minute <= NOW()`) is the same TRUE/FALSE value as under `SET TIME ZONE 'UTC'`.
- The absolute instant Postgres stores is invariant; only the rendering changes per session.

If the two queries return different boolean results, the migration is wrong — STOP and investigate.

This step has no commit. Record the result in the PR description.

- [ ] **Step 5: Verify only the intended files changed**

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

Expected: 8 commits roughly aligned with the 8 tasks (Task 0 contributes none, Task 2 contributes 2). About 19 files changed total. No incidental drift.

If anything unexpected appears, audit before pushing the PR.

---

## Pre-PR push checklist (after Task 8 succeeds)

- [ ] `git push origin feat/utc-timestamps`
- [ ] `gh pr create` with title `refactor(timestamps): tz-aware datetime columns (timestamptz)` and a body that includes:
  - Spec link (`docs/dev/specs/2026-05-28-utc-timestamps-design.md`).
  - List of all 22 changed columns (one bullet per column, model.col → new type).
  - Note the `postgresql_using` hand-edit + explicit exception to the "no hand-edited migrations" rule (mirroring PR #156's `server_default` injection).
  - Confirm zero frontend changes.
  - Single `alembic upgrade head` is the only deploy step.
- [ ] Comment `/ci` on the PR.
- [ ] Once CI passes: follow the `pr-codex-review-loop` skill on PR. Local codex follow-up if @codex subscription quota is hit.
- [ ] After codex clean + CI green: squash-merge + delete branch + remove worktree.
