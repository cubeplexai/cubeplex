# Project-wide UTC timestamps — design

**Date:** 2026-05-28
**Branch:** `feat/utc-timestamps`
**Related:** PR #156 (sandbox pause/resume) surfaced this as documented nit #11
during the local codex review pass.

## Problem & motivation

cubeplex originated on MySQL where naïve `DATETIME` was the standard storage
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

Cubeplex hasn't shipped publicly, so we cut over cleanly with a single PR.

## Goals

- Every `datetime` column in cubeplex is Postgres `timestamptz` (mapped to
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
  column in place; for cubeplex's current row counts this is fine.
- A guard / lint rule that auto-detects future regressions. Trust the
  CLAUDE.md hard rule.

## Scope

22 `datetime` columns across 11 model files (audit timestamp: 2026-05-28):

| Model file | Columns |
|---|---|
| `mixins.py` (CubeplexBase) | `created_at`, `updated_at` (inherited by ~all tables) |
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
`sa_column=Column(DateTime(timezone=True), ...)`. Example (CubeplexBase):

```python
from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

class CubeplexBase(SQLModel):
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

**Indexed columns** — covered by per-Field `index=True` (lifts into
`Column(index=True)`) AND by table-level `Index` declarations in
`__table_args__`:

| Form | Where | Migration concern |
|---|---|---|
| `Field(index=True)` | `user_sandbox.in_use_until`, `user_sandbox.last_provider_check` | Move `index=True` into the new `Column(...)` call |
| `__table_args__ = (Index("ix_...", "col"), ...)` | `invite_token.expires_at` (`Index("ix_invite_tokens_expires", ...)`) | `__table_args__` unchanged — autogen's `alter_column` rebuilds the index against the new column type; verify with the round-trip |
| `Index` with predicate ("partial index") | Comment in `conversation.py:29` references the pattern but no actual partial index is declared | None — the comment is informational |

Example for the per-Field case:

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
> ISO 8601 (either `+00:00` via `utc_isoformat()` or `Z` via Pydantic
> default — both valid). No naïve `datetime` ever crosses the DB or
> service-API boundary. When introducing a new datetime column or
> converting an existing one, the alembic migration must hand-add
> `postgresql_using="<col> AT TIME ZONE 'UTC'"` to the `alter_column` call
> — autogen omits this, and the default `USING column::timestamptz`
> behaviour applies the session `TimeZone` (wrong for our stored UTC
> values).

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

- `backend/cubeplex/repositories/invite_token.py:30-32`
- `backend/cubeplex/repositories/egress_ref.py:33-34`
- `backend/cubeplex/mcp/effective.py:562-563`
- `backend/cubeplex/mcp/oauth/token_manager.py:339-340`

Each looks like:
```python
if x.tzinfo is None:
    x = x.replace(tzinfo=UTC)
```

All are guarding DB-read datetimes that were previously naïve. After the
migration, the DB returns tz-aware values; these branches are dead.

**4.2 Tighten `utc_isoformat()`:**

`backend/cubeplex/utils/time.py` switches from idempotent fallback to a
loud assertion:

```python
def utc_isoformat(dt: datetime) -> str:
    """Return ISO 8601 with UTC offset. Asserts the datetime is tz-aware —
    after the timestamptz migration, every datetime in cubeplex is tz-aware
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
`backend/cubeplex/sandbox/manager.py` G11 grace block (around the
`pause_idle_age = (datetime.now(UTC) - last_activity).total_seconds()`
section, which defensively does `if last_activity.tzinfo is None:
last_activity = last_activity.replace(tzinfo=UTC)`). Delete the defense;
the DB now returns tz-aware.

Estimated 5-10 such defensive blocks across the codebase. Each individually
trivial; collectively a grep + delete pass.

**4.4 Test fixtures using naïve datetimes:**

- `backend/tests/e2e/test_sandbox_lease.py:118,155` — `datetime.utcnow()`
  replace with `datetime.now(UTC)`. If any `_as_naive_utc` helper exists
  from when naïve comparisons were needed, delete it.
- `backend/tests/unit/test_egress_exchange_service.py:111,121,194,202` —
  this fixture explicitly stores naïve `expires_at` to "simulate what
  Postgres returns". After the migration that simulation is wrong; the
  fixture needs to seed tz-aware values, and the assertions should
  compare tz-aware to tz-aware. The repo-side defensive
  `if exp.tzinfo is None: exp = exp.replace(tzinfo=UTC)` block
  (egress_ref.py:33-34) is deleted by §4.1, so the fixture must hand the
  service a tz-aware datetime to begin with — otherwise the test
  exposes a pre-existing latent bug.

### 5. Frontend / API surface

**Mostly unchanged, with one wire-format normalisation.**

- `utc_isoformat()` keeps outputting `"...+00:00"`; the 35 call sites that
  use it serialise identically before and after the migration.
- Two Pydantic v2 response schemas declare `datetime` fields directly
  (`backend/cubeplex/api/schemas/provider.py:133-134, 181-182` —
  `created_at`/`updated_at`; `backend/cubeplex/api/schemas/mcp.py:25, 105`
  — `expires_at`). FastAPI/Pydantic v2 default-serialises datetimes:
  - **Pre-migration**: column is naïve → Pydantic emits
    `"2026-05-28T11:30:54.000000"` (no offset). A frontend parsing this
    as local time would be silently wrong on any non-UTC client. This is
    a pre-existing bug.
  - **Post-migration**: column is tz-aware UTC → Pydantic emits
    `"2026-05-28T11:30:54.000000Z"`. Unambiguous and parseable.

The `Z` suffix vs `+00:00` is a cosmetic difference between
`utc_isoformat()` (returns `+00:00`) and Pydantic's default (`Z`); both
are valid ISO 8601 and both are accepted by JavaScript `Date.parse` /
`new Date()`. We accept this inconsistency rather than force every schema
through `utc_isoformat`; the migration's job is to make the *underlying
datetime* unambiguous, not to unify suffix style.

Pydantic v2 ISO 8601 parsing on inbound API datetime fields already
accepts both `+00:00` and `Z` (and naïve, treating as UTC — fine because
inbound is rare; clients send tz-aware).

### 6. Testing strategy

The migration is "same semantics, different storage type"; no behavior
change. No new test files. Existing tests are the regression net.

Validation steps:

1. `cd backend && uv run pytest -q` — all green.
2. `cd backend && uv run mypy cubeplex/` — clean.
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

- `backend/cubeplex/utils/time.py` — current `utc_isoformat()`.
- `backend/cubeplex/models/mixins.py` — `CubeplexBase` (cascades to most tables).
- `backend/cubeplex/repositories/user_sandbox.py` — the reaper SQL
  introduced in PR #156 that motivated this fix.
- `docs/dev/notes/2026-05-28-opensandbox-pause-resume-internals.md` —
  source of nit #11 that surfaced this.
- Postgres `timestamptz` semantics:
  [PostgreSQL docs: Date/Time Types](https://www.postgresql.org/docs/current/datatype-datetime.html).
