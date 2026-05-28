# Project-wide UTC timestamps — design

**Date:** 2026-05-28
**Branch:** `feat/utc-timestamps`
**Related:** PR #156 (sandbox pause/resume) surfaced this as documented nit #11
during the local codex review pass.

## Problem & motivation

cubebox originated on MySQL where naïve `DATETIME` was the standard storage
type. Migrating to Postgres kept the convention: every `datetime` field in
every SQLModel is mapped to `timestamp without time zone`. Python writes
`datetime.now(UTC)` (tz-aware) which gets stored as naïve UTC clock values;
reads come back naïve and callers re-attach UTC defensively to do
comparisons / arithmetic.

This works **as long as** Postgres session `TimeZone` stays UTC. The moment
an operator (DBA, ops migration, local psql session, a future replica with
a different default) sets `TimeZone='Asia/Shanghai'` (or anything non-UTC),
the comparison `naive_column + INTERVAL '...' <= NOW()` silently drifts by
the offset because Postgres interprets the naïve column as
session-local-time. Reapers fire too early or too late by the offset; DST
shifts the same way.

The sandbox pause/resume work (PR #156) introduced several `paused_at + ttl
* INTERVAL '1 second' <= NOW()` reapers that depend on this implicit
invariant. Codex flagged it as nit #11. The user's decision:

> All time fields, the DB should store UTC. Application/frontend handles
> timezone formatting. No backward compatibility needed.

Cubebox hasn't shipped publicly, so we cut over cleanly with a single PR.

## Goals

- Every `datetime` column in cubebox is Postgres `timestamptz` (mapped to
  SQLAlchemy `DateTime(timezone=True)`).
- Postgres stores absolute UTC instants regardless of session `TimeZone`.
- Reads come back tz-aware UTC; no Python-side `replace(tzinfo=UTC)` needed.
- Reaper SQL (`column + INTERVAL ... <= NOW()`) is correct in any session
  `TimeZone`.
- Wire format to the frontend stays unchanged (ISO 8601 with `+00:00`).
- Code path discipline: a naïve `datetime` reaching `utc_isoformat()` is a
  loud assertion failure, not a silent fix.

## Non-goals

- Frontend changes (wire format identical).
- Multi-tenant / time-zone-aware UI features (independent feature, not
  what this is about).
- Backward compatibility shims (project hasn't shipped publicly).
- Performance tuning. `ALTER COLUMN ... TYPE timestamptz` rewrites the
  column in place; for cubebox's current row counts this is fine.
- A guard / lint rule that auto-detects future regressions. Trust the
  CLAUDE.md hard rule.

## Scope

22 `datetime` columns across 11 model files (audit timestamp: 2026-05-28):

| Model file | Columns |
|---|---|
| `mixins.py` (CubeboxBase) | `created_at`, `updated_at` (inherited by ~all tables) |
| `user_sandbox.py` | `last_activity_at`, `paused_at`, `last_resumed_at`, `in_use_until`, `last_provider_check` |
| `provider.py` | `last_liveness_at`, `last_test_at` |
| `memory.py` | `last_used_at` |
| `skill.py` | `deprecated_at`, `installed_at`, `hidden_at` |
| `invite_token.py` | `expires_at`, `used_at` |
| `conversation.py` | `deleted_at` |
| `billing.py` | `started_at`, `ended_at` |
| `egress_ref.py` | `expires_at` |
| `attachment.py` | `attached_at` |
| `mcp.py` | `last_discovered_at`, `expires_at` |

## Design

### 1. Column convention

Each `datetime` `Field` is explicitly annotated with
`sa_column=Column(DateTime(timezone=True), ...)`. Example (CubeboxBase):

```python
from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

class CubeboxBase(SQLModel):
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
```

Existing per-Field options (`index=True`, `nullable`, foreign keys) move
into the `Column(...)` call. The Python type annotation (`datetime` /
`datetime | None`) is unchanged. `default_factory` already produces
tz-aware values everywhere in the codebase, so no write-side change is
needed.

Two corner-case shapes the migration must handle:

**Indexed column** (`user_sandbox.in_use_until`,
`user_sandbox.last_provider_check`):
```python
in_use_until: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
)
```

**Bare type-annotation columns** (currently `attachment.attached_at`,
`mcp.last_discovered_at`, `mcp.expires_at` — `: datetime | None = None`
with no `Field()`):
```python
attached_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```
Bare annotations rely on SQLModel inferring `DateTime()` from the type;
that inference still defaults to naïve, so these need an explicit
`Field()` + `sa_column` to participate in the migration.

### 2. CLAUDE.md hard rule

A new entry under **Hard Code Rules**:

> **Time columns are tz-aware.** All `datetime` model fields use
> `sa_column=Column(DateTime(timezone=True), ...)` (Postgres `timestamptz`).
> Application code writes `datetime.now(UTC)` (tz-aware). Frontend gets
> ISO 8601 with `+00:00` offset via `utc_isoformat()`. No naïve `datetime`
> ever crosses the DB or service-API boundary.

### 3. Alembic migration

One revision: `utc_timestamps_migration`. Procedure:

1. Change all 22 fields per §1.
2. Run `uv run alembic revision --autogenerate -m "utc timestamps"`. Autogen
   emits 22 `op.alter_column` calls.
3. **Surgical hand-edit**: add `postgresql_using="<col> AT TIME ZONE 'UTC'"`
   to each of the 22 `alter_column` calls (autogen leaves it off, which
   would make Postgres convert via session `TimeZone` — wrong for our
   stored naïve UTC values). Symmetrically, the downgrade `alter_column`
   gets `postgresql_using="<col> AT TIME ZONE 'UTC'"` to strip back to naïve.
4. Verify: `alembic upgrade head` → `alembic downgrade -1` → `alembic
   upgrade head`. Each step succeeds with no errors.

This is a known exception to the "do not hand-edit migrations" rule — same
class of intervention as the `server_default` injection PR #156 did for the
pause/resume columns. The exception is recorded in the migration's
docstring.

#### Migration template per column

```python
op.alter_column(
    "user_sandboxes",
    "last_activity_at",
    type_=sa.DateTime(timezone=True),
    existing_type=sa.DateTime(),
    existing_nullable=False,
    postgresql_using="last_activity_at AT TIME ZONE 'UTC'",
)
# downgrade
op.alter_column(
    "user_sandboxes",
    "last_activity_at",
    type_=sa.DateTime(),
    existing_type=sa.DateTime(timezone=True),
    existing_nullable=False,
    postgresql_using="last_activity_at AT TIME ZONE 'UTC'",
)
```

### 4. Code cleanup

After the column type change, several Python-side workarounds become
redundant or wrong:

**4.1 Delete naïve-fallback defense blocks (4 places):**

- `backend/cubebox/repositories/invite_token.py:30-32`
- `backend/cubebox/repositories/egress_ref.py:33-34`
- `backend/cubebox/mcp/effective.py:562-563`
- `backend/cubebox/mcp/oauth/token_manager.py:339-340`

Each looks like:
```python
if x.tzinfo is None:
    x = x.replace(tzinfo=UTC)
```

All are guarding DB-read datetimes that were previously naïve. After the
migration, the DB returns tz-aware values; these branches are dead.

**4.2 Tighten `utc_isoformat()`:**

`backend/cubebox/utils/time.py` switches from idempotent fallback to a
loud assertion:

```python
def utc_isoformat(dt: datetime) -> str:
    """Return ISO 8601 with UTC offset. Asserts the datetime is tz-aware —
    after the timestamptz migration, every datetime in cubebox is tz-aware
    by construction; a naïve dt here means someone violated the hard rule."""
    assert dt.tzinfo is not None, (
        f"naïve datetime reached utc_isoformat: {dt!r}; should be tz-aware"
    )
    return dt.isoformat()
```

The 35 call sites are unchanged.

**4.3 Audit DB-read datetime comparison sites:**

Grep for `record.<datetime_field>` patterns participating in Python-side
comparisons or subtraction with `datetime.now(...)`. Known one:
`backend/cubebox/sandbox/manager.py` G11 grace block (around the
`pause_idle_age = (datetime.now(UTC) - last_activity).total_seconds()`
section, which defensively does `if last_activity.tzinfo is None:
last_activity = last_activity.replace(tzinfo=UTC)`). Delete the defense;
the DB now returns tz-aware.

Estimated 5-10 such defensive blocks across the codebase. Each individually
trivial; collectively a grep + delete pass.

**4.4 Test fixtures using `datetime.utcnow()`:**

`backend/tests/e2e/test_sandbox_lease.py:118,155` (introduced by PR #156).
Replace with `datetime.now(UTC)`. If any `_as_naive_utc` helper exists from
when naïve comparisons were needed, delete it.

### 5. Frontend / API surface

Unchanged. `utc_isoformat(datetime.now(UTC))` produces
`"2026-05-28T10:30:00.123456+00:00"`, same as before. Pydantic ISO 8601
parsing on inbound API datetime fields already accepts tz-aware values.

### 6. Testing strategy

The migration is "same semantics, different storage type"; no behavior
change. No new test files. Existing tests are the regression net.

Validation steps:

1. `cd backend && uv run pytest -q` — all green.
2. `cd backend && uv run mypy cubebox/` — clean.
3. `cd backend && uv run alembic upgrade head` then `alembic downgrade -1`
   then `alembic upgrade head` — each step succeeds.
4. **Manual TimeZone smoke test** (one-off, recorded in plan but not
   automated):
   ```sql
   SET TIME ZONE 'Asia/Shanghai';
   SELECT NOW(), paused_at, paused_at + INTERVAL '1 minute' <= NOW()
   FROM user_sandboxes LIMIT 1;
   ```
   `paused_at` returns with `+00:00` offset (not `+08:00`); arithmetic
   result identical to running the same query under `SET TIME ZONE 'UTC'`.

### 7. Rollout / PR scope

Single PR. ~19 files + 1 migration. No phasing, no compat layer. Operator
runs `alembic upgrade head` once at deploy; storage rewrite is in-place
(`ALTER COLUMN ... TYPE timestamptz USING ... AT TIME ZONE 'UTC'`) and
acceptable for current row counts.

PR description must:
- List all 22 changed columns (reviewer can scan scope at a glance).
- Note the `postgresql_using` hand-edit + the explicit exception to the
  "no hand-edited migrations" rule (mirroring the pause/resume PR's
  `server_default` injection).
- Confirm zero frontend changes.
- Document the deploy step (single `alembic upgrade head`).

## References

- `backend/cubebox/utils/time.py` — current `utc_isoformat()`.
- `backend/cubebox/models/mixins.py` — `CubeboxBase` (cascades to most tables).
- `backend/cubebox/repositories/user_sandbox.py` — the reaper SQL
  introduced in PR #156 that motivated this fix.
- `docs/dev/notes/2026-05-28-opensandbox-pause-resume-internals.md` —
  source of nit #11 that surfaced this.
- Postgres `timestamptz` semantics:
  [PostgreSQL docs: Date/Time Types](https://www.postgresql.org/docs/current/datatype-datetime.html).
