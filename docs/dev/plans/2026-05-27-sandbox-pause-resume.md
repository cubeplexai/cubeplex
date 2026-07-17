# Sandbox Pause/Resume Implementation Plan

> For agentic workers: execute tasks top-to-bottom. Each task is TDD —
> write the test first, watch it fail, implement, watch it pass. Stay on
> branch `feat/sandbox-pause-resume`; never switch to main or merge
> mid-execution. Read `.worktree.env` before running anything: this slot
> uses backend port **8058** and DB `cubeplex_feat_sandbox_pause_resume`;
> tests auto-route to `cubeplex_test_feat_sandbox_pause_resume`. Run tests
> with plain `uv run pytest` from `backend/`. Do not hand-edit Alembic
> migrations — always `alembic revision --autogenerate`. Commit per task;
> never push, never amend, never invoke codex during execution.

## Goal

Add `paused` as a first-class sandbox lifecycle state with native
OpenSandbox pause/resume, so an idle sandbox freezes its compute (state
preserved) instead of being killed, and a reused sandbox resumes in ~1s
with files and session intact. Capability-gated (`supports_pause()`) so a
driver without native pause keeps today's kill-on-idle behaviour.

## Architecture

- **States** (stored in `UserSandbox.status`, str(20)):
  `running → pausing → paused → resuming → running`, plus terminal
  `terminated` / `failed`. `pausing`/`resuming` are short-lived in-flight
  guards; only `running` is directly acquirable.
- **In-use lease** (`in_use_until`): an operation that holds the sandbox
  sets a future lease; the idle reaper requires *both* clock-idle
  (`last_activity_at + ttl_seconds <= now`) *and* unleased
  (`in_use_until IS NULL OR in_use_until < now`). Stops a long op from
  being paused mid-flight even when `last_activity_at` is stale.
- **Atomic pause claim** (`claim_pausing`): a single conditional UPDATE
  `running → pausing` that re-asserts idleness + status + lease in its
  WHERE clause. The reaper calls provider `pause()` only if the UPDATE
  changed a row, so the row already reads `pausing` (unacquirable) before
  the slow suspend starts. Two reapers can't both win.
- **Resume is a manager-level factory**, not a method on a dead handle:
  the provider method is `connect_or_resume(sandbox_id)` (unified shape
  across providers per OQ-5). OpenSandbox impl calls `Sandbox.resume(...)`
  (the SDK classmethod that re-resolves execd/egress endpoints and rebuilds
  adapters) then re-binds; e2b impl calls `connect`, which auto-resumes.
  `Sandbox.connect_or_resume(...)` is a driver classmethod; the manager
  discards the old handle, re-applies egress, health-probes, marks `running`.
- **Two reaper passes** in the existing cleanup loop:
  `pause_idle()` (pause-capable providers) and `reap_paused()` (hard-kill
  paused rows past `paused_ttl_seconds`). Non-capable providers fall back
  to `cleanup_expired()` kill.
- **Provider scope (v1).** The manager today is OpenSandbox-only: it
  constructs `opensandbox.Sandbox` / `OpenSandbox` directly, with no
  provider-dispatch layer. This plan keeps that. The capability gate is
  enforced per-driver via `backend.supports_pause()` on the connected
  handle — `pause_idle()` checks it and falls back to kill when `False`
  (so `LocalSandbox` and any future non-pausing driver are safe) — not via
  a `provider` registry. The new `provider` column is recorded for forensics
  and a future multi-provider dispatch, but v1 reads/writes only
  `"opensandbox"`; do not branch manager logic on it yet.

## Tech Stack

FastAPI + async SQLModel/SQLAlchemy (Postgres), Alembic autogenerate,
`opensandbox` SDK (`Sandbox.pause()` / `Sandbox.resume(...)` classmethod /
`SandboxState` constants), pytest + pytest-asyncio (`-m e2e` against a real
OpenSandbox; unit tests for state-machine races). mypy strict, 100-char
lines.

---

## Task 0 — Read OpenSandbox SDK pause/resume internals (mandatory)

Before touching code, read the SDK to ground every decision in real behaviour
and keep `docs/dev/notes/2026-05-28-opensandbox-pause-resume-internals.md` up
to date as new findings surface.

**Files**
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/sandbox.py`
  (`Sandbox.pause`, `Sandbox.resume` classmethod, `Sandbox.connect`).
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/services/sandbox.py`
  (`pause_sandbox`, `resume_sandbox`, `get_sandbox_endpoint`,
  `renew_sandbox_expiration`, `get_info`).
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/adapters/sandboxes_adapter.py`
  (concrete HTTP impl + response handling).
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/api/lifecycle/api/sandboxes/post_sandboxes_sandbox_id_pause.py`
  and `…_resume.py` (response codes 202/401/403/404/409/500).
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/api/lifecycle/models/sandbox_status.py`
  (`SandboxStatus` wire format incl. `Resuming`).
- Read: `backend/.venv/lib/python3.13/site-packages/opensandbox/models/sandboxes.py`
  (local `SandboxState` constants — note `Resuming` is missing).
- Read: `docs/dev/notes/2026-05-28-opensandbox-pause-resume-internals.md` —
  the gotcha checklist that drives later tasks.

**Steps**

- [ ] **Step 1: Read the SDK files above and the notes file end-to-end.**
- [ ] **Step 2: Verify each gotcha (G1–G10) against the source** — confirm,
      contradict, or refine. If the SDK has changed since the note was
      written, edit the note (don't trust stale claims).
- [ ] **Step 3: For each open follow-up in the note (pause/resume latency,
      paused TTL counting, 409 body shape, egress persistence, 404 on GC'd
      sandbox), run a real probe against the running OpenSandbox in this
      worktree and append findings + raw evidence to the note's
      "Open follow-ups" section.** Empirical data beats speculation.

      Example probe (adjust to your env):

      ```bash
      cd /home/chris/cubeplex/.worktrees/feat/sandbox-pause-resume
      uv run python - <<'PY'
      # exercise pause/resume against the real OpenSandbox configured for
      # this worktree, measuring per-step latency and printing get_info()
      # transitions so the note can quote real numbers.
      PY
      ```

- [ ] **Step 4: Commit the updated note (if changed).**

      ```bash
      git add docs/dev/notes/2026-05-28-opensandbox-pause-resume-internals.md
      git commit -m "docs(notes): refine OpenSandbox pause/resume gotchas with empirical findings (#145)"
      ```

      If the SDK matched the note exactly, commit only the empirical
      follow-up data; if it diverged, fix the body too.

No code in this task. **All later tasks (especially Task 4 provider
capabilities, Task 5 manager pause/resume, Task 7 E2E) must reference
specific gotchas from this note in their commit messages or PR description
when the design choice maps to a documented trap (e.g. "G2: replace handle
on resume", "G1: do not advance to `paused` synchronously").**

---

## Task 1 — Add lifecycle columns to `UserSandbox`

**Files**
- Modify: `backend/cubeplex/models/user_sandbox.py`
- Test: `backend/tests/unit/test_user_sandbox_model.py` (Create)

**Steps**

1. Write the failing test first:

```python
# backend/tests/unit/test_user_sandbox_model.py
from cubeplex.models.user_sandbox import UserSandbox


def test_new_lifecycle_columns_default():
    row = UserSandbox(
        user_id="u_1",
        sandbox_id="sbx_abc",
        image="ubuntu:22.04",
    )
    assert row.status == "running"
    assert row.provider == "opensandbox"
    assert row.paused_at is None
    assert row.last_resumed_at is None
    assert row.in_use_until is None
    assert row.paused_ttl_seconds == 24 * 60
```

2. Run it, confirm it fails (`AttributeError`/`TypeError` on the new
   fields):

```bash
cd backend && uv run pytest tests/unit/test_user_sandbox_model.py -q
# EXPECTED: errors — UserSandbox has no field 'provider' / 'paused_at' / ...
```

3. Add the columns to `UserSandbox`:

```python
    status: str = Field(default="running", max_length=20)
    image: str = Field(max_length=512)
    volumes_config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = Field(default=3600)
    # The two NOT NULL additions carry server_defaults so the autogenerated
    # migration backfills existing user_sandboxes rows (a Python-side default
    # alone does not touch rows already in the table). Nullable columns
    # (paused_at, last_resumed_at, in_use_until) need no server_default.
    provider: str = Field(
        default="opensandbox", max_length=32,
        sa_column_kwargs={"server_default": "opensandbox"},
    )
    paused_at: datetime | None = Field(default=None)
    paused_ttl_seconds: int = Field(  # 24 minutes (OQ-2)
        default=24 * 60, sa_column_kwargs={"server_default": "1440"},
    )
    last_resumed_at: datetime | None = Field(default=None)
    in_use_until: datetime | None = Field(default=None, index=True)
```

4. Re-run, confirm green:

```bash
cd backend && uv run pytest tests/unit/test_user_sandbox_model.py -q
# EXPECTED: 1 passed
```

5. Commit:

```bash
git commit -m "feat(sandbox): add pause/resume lifecycle columns to UserSandbox"
```

---

## Task 2 — Autogenerate the migration

**Files**
- Create: `backend/alembic/versions/<rev>_sandbox_pause_resume_columns.py`
  (generated, never hand-written)

**Steps**

1. Bring the worktree DB to head, then autogenerate:

```bash
cd backend && uv run alembic upgrade head
uv run alembic revision --autogenerate -m "sandbox pause resume columns"
# EXPECTED: "Generated .../versions/<rev>_sandbox_pause_resume_columns.py"
```

2. Inspect the generated file (read only — do not edit). Confirm it adds
   `provider`, `paused_at`, `paused_ttl_seconds`, `last_resumed_at`,
   `in_use_until` to `user_sandboxes` and the `ix_..._in_use_until` index,
   with matching downgrade `drop_column`s. **Verify the two NOT NULL columns
   (`provider`, `paused_ttl_seconds`) carry a `server_default`** in the
   `add_column` calls — that is what backfills existing rows; without it the
   upgrade fails on a non-empty table. If they don't, the `server_default` is
   missing from the model (Task 1) — fix the *model*, downgrade, delete the bad
   revision file, and re-autogenerate. If the autogen diff is otherwise wrong
   (e.g. unrelated drift), fix the *model* the same way — do not patch the
   migration body.

3. Apply and verify it round-trips:

```bash
cd backend && uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
# EXPECTED: each step "Running upgrade/downgrade ... <rev>" with no error
```

4. Commit:

```bash
git commit -m "feat(sandbox): migration for pause/resume columns"
```

---

## Task 3 — Repository transitions + atomic `claim_pausing`

**Files**
- Modify: `backend/cubeplex/repositories/user_sandbox.py`
- Test: `backend/tests/e2e/test_user_sandbox_repo_transitions.py`
  (Create — uses the real per-slot test DB so the raw UPDATE is exercised)

> **Fixtures — there is no `tests/integration/` tree.** This repo has no
> `tests/integration/` directory and no `db_session` / `org_ctx` fixtures
> there. Repo-layer DB tests live under `tests/e2e/` and use the existing
> `db_session` fixture in `tests/e2e/conftest.py` (a raw `AsyncSession` on the
> per-slot test DB). There is **no** `org_ctx` fixture — construct the scoped
> repo explicitly as `UserSandboxRepository(db_session, org_id=..., workspace_id=...)`.
> `UserSandbox` rows carry real FKs (`user_id` → `users.id`, plus `org_id` /
> `workspace_id` from `OrgScopedMixin`), so seed an org + workspace + user
> first (mirror how an existing `tests/e2e` repo test seeds its parent rows,
> e.g. via the auth/register HTTP flow or a shared seed helper) and use those
> ids — do not invent free-floating `"u_1"` / `"o_1"` ids that violate the FK.

**Steps**

1. Write failing E2E DB tests covering: (a) `claim_pausing` flips a
   stale-idle unleased `running` row to `pausing` and returns `True`;
   (b) a second `claim_pausing` on the now-`pausing` row returns `False`
   (single-winner); (c) a row whose `in_use_until` is in the future is
   **not** claimed; (d) a row whose `last_activity_at` is fresh is **not**
   claimed; (e) `get_active_by_user` ignores `pausing`/`paused`;
   (f) `get_resumable_by_user` returns a `paused` row but never
   `pausing`/`resuming`; (g) `mark_paused`/`mark_resuming`/`mark_running`
   reject illegal prior states.

```python
# backend/tests/e2e/test_user_sandbox_repo_transitions.py
from datetime import UTC, datetime, timedelta

import pytest

from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository

pytestmark = pytest.mark.e2e


# `scope` is (org_id, workspace_id, user_id) seeded into the test DB by a
# local fixture (see the note above): create an org + workspace + user, yield
# their ids. `repo.add` stamps org_id/workspace_id from the scoped repo, so the
# seeded user_id must be a real users.id row.
async def _mk(repo, session, scope, *, status="running", idle_secs=10, lease=None):
    row = UserSandbox(
        user_id=scope["user_id"], sandbox_id=f"sbx_{datetime.now(UTC).timestamp()}",
        image="img", status=status, ttl_seconds=1,
        last_activity_at=datetime.now(UTC) - timedelta(seconds=idle_secs),
        in_use_until=lease,
    )
    row = await repo.add(row)
    await session.commit()
    return row


async def test_claim_pausing_single_winner(db_session, scope):
    repo = UserSandboxRepository(db_session, org_id=scope["org_id"],
                                 workspace_id=scope["workspace_id"])
    row = await _mk(repo, db_session, scope)
    assert await repo.claim_pausing(row.id) is True
    assert await repo.claim_pausing(row.id) is False  # already pausing


async def test_claim_pausing_skips_leased(db_session, scope):
    repo = UserSandboxRepository(db_session, org_id=scope["org_id"],
                                 workspace_id=scope["workspace_id"])
    lease = datetime.now(UTC) + timedelta(minutes=5)
    row = await _mk(repo, db_session, scope, lease=lease)
    assert await repo.claim_pausing(row.id) is False


async def test_claim_pausing_skips_fresh_activity(db_session, scope):
    repo = UserSandboxRepository(db_session, org_id=scope["org_id"],
                                 workspace_id=scope["workspace_id"])
    row = await _mk(repo, db_session, scope, idle_secs=0)
    assert await repo.claim_pausing(row.id) is False


async def test_get_active_ignores_paused(db_session, scope):
    repo = UserSandboxRepository(db_session, org_id=scope["org_id"],
                                 workspace_id=scope["workspace_id"])
    await _mk(repo, db_session, scope, status="paused")
    assert await repo.get_active_by_user(scope["user_id"]) is None
```

   Define the `scope` fixture locally (seed org + workspace + user, yield their
   ids); mirror how an existing `tests/e2e` repo test seeds its parent rows.

2. Run, confirm failure:

```bash
cd backend && uv run pytest tests/e2e/test_user_sandbox_repo_transitions.py -q
# EXPECTED: errors — UserSandboxRepository has no attribute 'claim_pausing'
```

3. Implement the repository methods. The claim is **one** atomic UPDATE so
   two reapers can't both win:

```python
from sqlalchemy import update

    async def claim_pausing(self, record_id: str) -> bool:
        """Atomically flip running -> pausing, re-asserting idleness + lease.

        Single conditional UPDATE: the idleness, status, and lease checks
        live in the WHERE clause so a touch/keepalive that lands between
        candidate selection and this claim makes it a no-op. Returns whether
        a row was claimed; the reaper calls provider pause() only on True.
        """
        stmt = (
            update(UserSandbox)
            .where(UserSandbox.id == record_id)
            # Re-assert the repo's (org_id, workspace_id) scope on every
            # transition UPDATE — these raw UPDATEs bypass _scoped_select(),
            # so the scope predicates must be added by hand or the invariant
            # leaks (a reaper could flip a row outside its scope).
            .where(UserSandbox.org_id == self.org_id)
            .where(UserSandbox.workspace_id == self.workspace_id)
            .where(UserSandbox.status == "running")
            .where(
                text(
                    "(in_use_until IS NULL OR in_use_until < NOW()) "
                    "AND last_activity_at + ttl_seconds * INTERVAL '1 second' <= NOW()"
                )
            )
            .values(status="pausing")
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount == 1

    async def _transition(self, record_id: str, frm: str, to: str, **extra: Any) -> bool:
        stmt = (
            update(UserSandbox)
            .where(UserSandbox.id == record_id)
            .where(UserSandbox.org_id == self.org_id)  # keep scope on raw UPDATE
            .where(UserSandbox.workspace_id == self.workspace_id)
            .where(UserSandbox.status == frm)
            .values(status=to, **extra)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount == 1

    async def mark_paused(self, record_id: str, *, paused_at: datetime | None = None) -> bool:
        return await self._transition(
            record_id, "pausing", "paused", paused_at=paused_at or datetime.now(UTC)
        )

    async def mark_resuming(self, record_id: str) -> bool:
        return await self._transition(record_id, "paused", "resuming")

    async def mark_running(self, record_id: str, *, last_resumed_at: datetime | None = None) -> bool:
        # Valid from pausing (pause failed -> revert) or resuming (resume ok).
        extra: dict[str, Any] = {}
        if last_resumed_at is not None:
            extra["last_resumed_at"] = last_resumed_at
        stmt = (
            update(UserSandbox)
            .where(UserSandbox.id == record_id)
            .where(UserSandbox.org_id == self.org_id)  # keep scope on raw UPDATE
            .where(UserSandbox.workspace_id == self.workspace_id)
            .where(UserSandbox.status.in_(("pausing", "resuming")))  # type: ignore[attr-defined]
            .values(status="running", **extra)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount == 1

    async def mark_failed(self, record_id: str) -> None:
        record = await self.get(record_id)
        if record:
            record.status = "failed"
            await self.session.commit()

    async def acquire_in_use(self, record_id: str, lease_seconds: int) -> None:
        record = await self.get(record_id)
        if record:
            record.in_use_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            await self.session.commit()

    async def release_in_use(self, record_id: str) -> None:
        record = await self.get(record_id)
        if record:
            record.in_use_until = None
            await self.session.commit()

    async def get_resumable_by_user(self, user_id: str) -> UserSandbox | None:
        """A running OR paused row for reuse; never a mid-transition row."""
        stmt = (
            self._scoped_select()
            .where(UserSandbox.user_id == user_id)
            .where(UserSandbox.status.in_(("running", "paused")))  # type: ignore[attr-defined]
            .order_by(UserSandbox.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

   Add `from datetime import timedelta` to the imports.
   `get_active_by_user` already filters `status == "running"` — leave it
   (it must not match `pausing`/`paused`, which it already doesn't).

4. Add the system-scope selection queries (classmethods, used by reapers):

```python
    @classmethod
    async def list_idle_to_pause_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """Running + clock-idle + unleased candidates. Reaper still re-claims
        each via claim_pausing before pausing, so a candidate touched between
        selection and claim is safely skipped."""
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' <= NOW()"))
            .where(text("(in_use_until IS NULL OR in_use_until < NOW())"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_paused_expired_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """Paused rows past paused_at + paused_ttl_seconds (hard-kill reaper)."""
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "paused")  # type: ignore[arg-type]
            .where(UserSandbox.paused_at.is_not(None))  # type: ignore[union-attr]
            .where(text("paused_at + paused_ttl_seconds * INTERVAL '1 second' <= NOW()"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

5. Re-run; confirm green:

```bash
cd backend && uv run pytest tests/e2e/test_user_sandbox_repo_transitions.py -q
# EXPECTED: all passed
```

6. Commit:

```bash
git commit -m "feat(sandbox): repo transitions + atomic claim_pausing"
```

---

## Task 4 — Provider capability methods (`supports_pause` / `pause` / `connect_or_resume`)

**Files**
- Modify: `backend/cubeplex/sandbox/base.py`
- Modify: `backend/cubeplex/sandbox/opensandbox.py`
- Modify: `backend/cubeplex/sandbox/local.py`
- Modify: `backend/cubeplex/sandbox/lazy.py`
- Test: `backend/tests/unit/test_sandbox_pause_capability.py` (Create)

**Steps**

1. Failing unit test — capability defaults + LocalSandbox no-op + base
   `pause` raising:

```python
# backend/tests/unit/test_sandbox_pause_capability.py
import pytest

from cubeplex.sandbox.base import Sandbox
from cubeplex.sandbox.local import LocalSandbox


def test_local_sandbox_does_not_support_pause():
    sb = LocalSandbox()
    assert sb.supports_pause() is False


@pytest.mark.asyncio
async def test_base_pause_raises_not_implemented():
    sb = LocalSandbox()
    with pytest.raises(NotImplementedError):
        await sb.pause()


@pytest.mark.asyncio
async def test_connect_or_resume_default_raises():
    with pytest.raises(NotImplementedError):
        await Sandbox.connect_or_resume("sbx_x")
```

2. Run, confirm failure:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_pause_capability.py -q
# EXPECTED: AttributeError: ... has no attribute 'supports_pause'
```

3. Add to `Sandbox` base (after `close`, before `BROWSER_PORT`):

```python
    def supports_pause(self) -> bool:
        """Whether this driver can natively pause/resume. Default False so the
        manager picks kill-on-idle for non-capable drivers."""
        return False

    async def pause(self) -> None:
        """Suspend this sandbox, preserving state. Override in capable drivers."""
        raise NotImplementedError("pause is not supported by this sandbox backend")

    @classmethod
    async def connect_or_resume(cls, sandbox_id: str, **kwargs: object) -> "Sandbox":
        """Connect to a sandbox, resuming it from `paused` if necessary, and
        return a fresh handle with re-resolved endpoints. OpenSandbox calls
        `Sandbox.resume(...)` server-side then connects; e2b's `connect`
        auto-resumes. Override in capable drivers."""
        raise NotImplementedError("connect_or_resume is not supported by this sandbox backend")
```

   Add `from __future__ import annotations` is already present; the
   `"Sandbox"` forward ref is fine.

4. OpenSandbox driver — implement all three. `connect_or_resume` delegates to
   the SDK `Sandbox.resume(...)` classmethod and wraps the result in `OpenSandbox`:

```python
# backend/cubeplex/sandbox/opensandbox.py
from datetime import timedelta
from opensandbox.config import ConnectionConfig

    def supports_pause(self) -> bool:
        return True

    async def pause(self) -> None:
        with _as_sandbox_error():
            await self._sandbox.pause()

    @classmethod
    async def connect_or_resume(
        cls,
        sandbox_id: str,
        *,
        conn_config: "ConnectionConfig | None" = None,
        resume_timeout: int = 30,
        workdir: str = "/workspace",
        **_: object,
    ) -> "OpenSandbox":
        with _as_sandbox_error():
            raw = await opensandbox.Sandbox.resume(
                sandbox_id,
                connection_config=conn_config,
                resume_timeout=timedelta(seconds=resume_timeout),
            )
        return cls(sandbox=raw, workdir=workdir)
```

5. `LazySandbox` — forward capability to the resolved backend; pause/resume
   on the lazy proxy is a no-op/forward (the manager drives transitions on
   concrete handles, not the lazy wrapper):

```python
    def supports_pause(self) -> bool:
        return self._sandbox.supports_pause() if self._sandbox is not None else False
```

   (`LocalSandbox` needs no change — base default `supports_pause()` →
   `False` and base `pause` raising are correct for it.)

6. Re-run; confirm green:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_pause_capability.py -q
# EXPECTED: 3 passed
```

7. Commit:

```bash
git commit -m "feat(sandbox): supports_pause/pause/connect_or_resume on Sandbox + drivers"
```

---

## Task 5 — Manager: resume-on-reuse, `pause_idle`, `reap_paused` + config knobs

**Files**
- Modify: `backend/cubeplex/sandbox/manager.py`
- Modify: `backend/config.yaml` (or the active config template — add the
  `sandbox.*` knobs with defaults: `pause_on_idle=true`,
  `idle_ttl_seconds=1800` (30 min, OQ-1), `paused_ttl_seconds=1440` (24 min,
  OQ-2), `lease_seconds=300` (5 min, OQ-7), `resume_timeout=30`)
- Test: `backend/tests/unit/test_sandbox_manager_pause.py` (Create)

**Steps**

1. Failing unit tests with a fake non-pausing driver + a mocked repo,
   covering: (a) `pause_idle` on a successful `claim_pausing` calls
   provider `pause()` then `mark_paused`; (b) `claim_pausing` False →
   provider `pause()` never called; (c) provider `pause()` raises →
   `mark_running` revert then kill fallback; (d) resume-on-reuse marks
   `resuming → mark_running` + `last_resumed_at`; resume raises → falls
   through to create-new (old row terminated/failed); (e) capability gap:
   a driver returning `supports_pause()==False` makes `pause_idle` a no-op
   for that row (kill path keeps).

   Keep these unit-level by mocking `opensandbox.Sandbox` and the repo (the
   real round-trip is the E2E in Task 7). Mirror the existing
   `tests/unit/test_sandbox_manager.py` style (`MagicMock` session factory).

2. Run, confirm failure:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_manager_pause.py -q
# EXPECTED: AttributeError: 'SandboxManager' has no attribute 'pause_idle'
```

3. Read the three new config knobs in `SandboxManager.__init__`:

```python
        # Defaults per spec OQ-1/OQ-2/OQ-7.
        self._pause_on_idle: bool = config.get("sandbox.pause_on_idle", True)
        self._idle_ttl_seconds: int = config.get("sandbox.idle_ttl_seconds", 30 * 60)
        self._paused_ttl_seconds: int = config.get("sandbox.paused_ttl_seconds", 24 * 60)
        self._resume_timeout: int = config.get("sandbox.resume_timeout", 30)
        # Lease lives in LazySandbox; acquire on entering a long op, renew during,
        # release on completion. Boundary is the long-operation boundary (execute,
        # browser start, file transfer), NOT agent-turn boundaries.
        self._lease_seconds: int = config.get("sandbox.lease_seconds", 5 * 60)
```

4. Extend `get_or_create` reuse path. After fetching the record use
   `get_resumable_by_user` instead of `get_active_by_user`, and branch on
   status:

```python
            record = await repo.get_resumable_by_user(user_id)

            if record and record.status == "paused":
                resumed = await self._resume_record(
                    session, repo, record, conn_config,
                    org_id=org_id, workspace_id=workspace_id, user_id=user_id,
                )
                if resumed is not None:
                    return resumed
                # resume genuinely failed (record marked failed inside
                # _resume_record) -> fall through to create-new. NOTE: a lost
                # mark_resuming race does NOT reach here; _resume_record waits
                # for the winner's row to become running and connects to it, so
                # two concurrent get_or_create calls never both create.
                record = None

            if record:  # running -> existing connect + health-check path (unchanged)
                ...
```

   New helper. The key race: two `get_or_create` calls both see the same
   `paused` row and both try to resume it. `mark_resuming` is a single
   conditional UPDATE (`paused → resuming`) so exactly one wins. The **loser**
   must not create a duplicate — it re-fetches and waits for the winner to flip
   the row to `running`, then connects to that same sandbox:

```python
    async def _resume_record(self, session, repo, record, conn_config, *,
                             org_id, workspace_id, user_id) -> "OpenSandbox | None":
        if not await repo.mark_resuming(record.id):
            # Lost the resume race (another caller already moved paused->resuming).
            # Do NOT fall through to create-new — that duplicates the sandbox.
            # Wait for the winner to reach running, then connect to the same row.
            return await self._await_resumed_by_winner(
                session, repo, record.id, conn_config,
                org_id=org_id, workspace_id=workspace_id, user_id=user_id,
            )
        try:
            backend = await OpenSandbox.connect_or_resume(
                record.sandbox_id,
                conn_config=conn_config,
                resume_timeout=self._resume_timeout,
                workdir=self._workdir,
            )
        except Exception as exc:
            logger.warning("Resume failed for {}: {}", record.sandbox_id, exc)
            await repo.mark_failed(record.id)
            if self._exchange_host:
                await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)
            return None
        await repo.mark_running(record.id, last_resumed_at=datetime.now(UTC))
        await repo.update_activity(record.id)
        if self._exchange_host:
            await self._apply_egress(
                session, backend, org_id=org_id, workspace_id=workspace_id,
                user_id=user_id, sandbox_id=record.sandbox_id,
            )
        return backend

    async def _await_resumed_by_winner(self, session, repo, record_id, conn_config, *,
                                       org_id, workspace_id, user_id) -> "OpenSandbox | None":
        """Race loser: poll the row until the winner flips it to running, then
        connect to that same sandbox. Returns None (caller creates new) only if
        the winner ended in failed/terminated, so we never duplicate."""
        deadline = datetime.now(UTC) + timedelta(seconds=self._resume_timeout)
        while datetime.now(UTC) < deadline:
            row = await repo.get(record_id)
            if row is None or row.status in ("failed", "terminated"):
                return None  # winner gave up -> caller may create a fresh one
            if row.status == "running":
                raw = await opensandbox.Sandbox.connect(
                    row.sandbox_id, connection_config=conn_config,
                )
                return OpenSandbox(sandbox=raw, workdir=self._workdir)
            await asyncio.sleep(0.5)  # still pausing/resuming
        return None  # timed out waiting for the winner; caller decides
```

   (Add `import asyncio` and `from datetime import timedelta` if not already
   imported.)

5. Add the two reaper entry points. `pause_idle` claims **before** the
   provider call, reverts + kills on failure:

```python
    async def pause_idle(self) -> None:
        """Pause idle, unleased sandboxes (capable providers). Replaces the
        kill-on-idle behaviour where supported."""
        if not self._pause_on_idle:
            await self.cleanup_expired()
            return
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            candidates = await UserSandboxRepository.list_idle_to_pause_system(session)
            for record in candidates:
                scoped = UserSandboxRepository(
                    session, org_id=record.org_id, workspace_id=record.workspace_id
                )
                if not await scoped.claim_pausing(record.id):
                    continue  # touched/acquired/already-claimed between select and claim
                try:
                    raw = await opensandbox.Sandbox.connect(
                        record.sandbox_id, connection_config=conn_config,
                        skip_health_check=True,
                    )
                    backend = OpenSandbox(sandbox=raw, workdir=self._workdir)
                    # Capability gate (spec): only natively-pausing drivers pause;
                    # everything else reverts and kills. v1 ships OpenSandbox only,
                    # so the gate is enforced via the driver's supports_pause(),
                    # not a provider registry the manager does not yet have.
                    if not backend.supports_pause():
                        await scoped.mark_running(record.id)  # revert pausing
                        await self._kill_record(session, scoped, record, conn_config)
                        continue
                    await backend.pause()
                    await scoped.mark_paused(record.id, paused_at=datetime.now(UTC))
                    logger.info("Paused idle sandbox {}", record.sandbox_id)
                except Exception as exc:
                    logger.warning("Pause failed for {}: {}; falling back to kill",
                                   record.sandbox_id, exc)
                    await scoped.mark_running(record.id)  # revert pausing -> running
                    await self._kill_record(session, scoped, record, conn_config)

    async def reap_paused(self) -> None:
        """Hard-kill paused rows past paused_ttl_seconds (24 min default,
        OQ-2), bounding stored state."""
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_paused_expired_system(session)
            for record in expired:
                scoped = UserSandboxRepository(
                    session, org_id=record.org_id, workspace_id=record.workspace_id
                )
                await self._kill_record(session, scoped, record, conn_config)
```

   Factor the kill+revoke+mark_terminated block from `cleanup_expired` into
   a shared `_kill_record(session, scoped_repo, record, conn_config)` and
   call it from `cleanup_expired`, `pause_idle` fallback, and `reap_paused`.

6. Re-run; confirm green:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_manager_pause.py -q
# EXPECTED: all passed
```

7. Commit:

```bash
git commit -m "feat(sandbox): manager resume-on-reuse + pause_idle/reap_paused + config knobs"
```

---

## Task 5b — Reconciler for stuck transient states (OQ-3)

A backend crash, a dropped 202 response, or an HTTP timeout can strand a row in
`pausing` or `resuming`. The provider is the source of truth: periodically
read `get_info().status` and repair the row. Also handles "provider says
`Running` but DB says `pausing`" (pause failed → revert) and `Failed`
propagation. Per the internals note (G3), the provider may return states the
local enum doesn't know about (e.g. `Resuming`) — handle unknown strings
gracefully.

**Files**
- Modify: `backend/cubeplex/models/user_sandbox.py` — add a
  `last_provider_check: datetime | None` column (autogen migration step).
- Modify: `backend/cubeplex/repositories/user_sandbox.py` — selection query
  `list_transient_for_reconcile_system` (status in (`pausing`, `resuming`)
  AND `last_provider_check` null/old).
- Modify: `backend/cubeplex/sandbox/manager.py` — `reconcile_transients()`
  entry point.
- Modify: `backend/cubeplex/sandbox/cleanup.py` — call `reconcile_transients`
  each loop, scan period 30 s (the existing 60 s loop is fine; the reconciler
  is idempotent and `claim_timeout`-bounded — see step 5 below).
- Test (unit): `backend/tests/unit/test_sandbox_reconciler.py` (Create).
- Test (E2E DB): `backend/tests/e2e/test_sandbox_reconciler.py` (Create) for
  the selection query and the column add.

**Steps**

1. **Add the column (autogen migration).** Write a failing test that asserts
   the column exists with default `None`, then add the column to the model and
   run `alembic revision --autogenerate -m "sandbox reconciler last_provider_check"`:

```python
# backend/cubeplex/models/user_sandbox.py
    last_provider_check: datetime | None = Field(default=None, index=True)
```

```bash
cd backend && uv run alembic upgrade head
uv run alembic revision --autogenerate -m "sandbox reconciler last_provider_check"
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
```

2. **Failing unit tests** — drive every branch the reconciler must handle:

```python
# backend/tests/unit/test_sandbox_reconciler.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubeplex.sandbox.manager import SandboxManager


@pytest.mark.asyncio
async def test_reconciler_pausing_to_paused_on_provider_paused():
    # DB row says 'pausing'; provider says 'Paused' -> mark_paused.
    ...


@pytest.mark.asyncio
async def test_reconciler_pausing_reverts_to_running_when_provider_running():
    # DB row says 'pausing'; provider says 'Running' -> mark_running revert
    # (pause failed). last_provider_check is updated regardless.
    ...


@pytest.mark.asyncio
async def test_reconciler_resuming_to_running_on_provider_running():
    # DB row says 'resuming'; provider says 'Running' -> mark_running.
    ...


@pytest.mark.asyncio
async def test_reconciler_propagates_failed():
    # provider says 'Failed' -> mark_failed; reason/message copied if present.
    ...


@pytest.mark.asyncio
async def test_reconciler_unknown_state_is_a_noop():
    # provider says 'Resuming' (missing from local SandboxState constants per
    # G3) or any other unknown string -> leave row, update last_provider_check.
    ...


@pytest.mark.asyncio
async def test_reconciler_explicit_trigger_runs_once():
    # Calling reconcile_transients() in a test bypasses the loop period.
    ...
```

3. **Selection query.** Add to `UserSandboxRepository`:

```python
    @classmethod
    async def list_transient_for_reconcile_system(
        cls, session: AsyncSession, *, claim_timeout: int = 60
    ) -> list[UserSandbox]:
        """Rows in transient states whose last_provider_check is stale (or
        null). claim_timeout bounds how often we re-poll a row that's still
        in flight — prevents hammering the provider every 30 s sweep."""
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status.in_(("pausing", "resuming")))  # type: ignore[attr-defined]
            .where(
                text(
                    "last_provider_check IS NULL "
                    "OR last_provider_check + :ct * INTERVAL '1 second' <= NOW()"
                )
            )
            .params(ct=claim_timeout)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

4. **Reconciler body.** Read provider state via the existing service adapter
   (`opensandbox.Sandbox.connect(..., skip_health_check=True)` → `get_info()`).
   Map state string → DB transition; unknown strings (including the
   API-documented `"Resuming"` that is missing from the local `SandboxState`
   constants per internals-note G3) are a no-op except for the
   `last_provider_check` bump:

```python
# backend/cubeplex/sandbox/manager.py
    async def reconcile_transients(self, *, claim_timeout: int = 60) -> None:
        """Repair rows stuck in pausing/resuming by reading provider state.

        - Paused      -> mark_paused (advance, OQ-3 / internals G1)
        - Running     -> mark_running (pause failed if DB was pausing;
                         resume succeeded if DB was resuming)
        - Failed      -> mark_failed (copy reason/message)
        - Terminated  -> mark_terminated + revoke egress
        - Pausing/Resuming/<unknown> -> no-op, bump last_provider_check
        """
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            rows = await UserSandboxRepository.list_transient_for_reconcile_system(
                session, claim_timeout=claim_timeout,
            )
            for record in rows:
                scoped = UserSandboxRepository(
                    session, org_id=record.org_id, workspace_id=record.workspace_id,
                )
                try:
                    raw = await opensandbox.Sandbox.connect(
                        record.sandbox_id, connection_config=conn_config,
                        skip_health_check=True,
                    )
                    info = await raw.get_info()
                    state = (info.status.state if info and info.status else "") or ""
                except Exception as exc:
                    logger.warning("Reconciler: get_info failed for {}: {}",
                                   record.sandbox_id, exc)
                    await scoped.touch_provider_check(record.id)
                    continue

                if state == "Paused":
                    await scoped.mark_paused(record.id, paused_at=datetime.now(UTC))
                elif state == "Running":
                    # Pause failed (DB was pausing) OR resume succeeded (DB was resuming).
                    # mark_running asserts prior status in ('pausing','resuming') so it
                    # handles both cases.
                    await scoped.mark_running(
                        record.id,
                        last_resumed_at=datetime.now(UTC) if record.status == "resuming" else None,
                    )
                elif state == "Failed":
                    await scoped.mark_failed(record.id)
                elif state == "Terminated":
                    await self._kill_record(session, scoped, record, conn_config)
                else:
                    # "Pausing" / "Resuming" / unknown -> let it continue
                    logger.debug("Reconciler: {} still {}", record.sandbox_id, state)

                await scoped.touch_provider_check(record.id)
```

   Add the `touch_provider_check(record_id)` helper on the repo (single UPDATE
   that sets `last_provider_check = NOW()`).

5. **Wire into the cleanup loop.** The existing `sandbox_cleanup_loop` runs
   every 60 s. Either add a faster sibling loop at 30 s, or call
   `reconcile_transients(claim_timeout=60)` each iteration of the existing
   60 s loop — the latter is simpler and `claim_timeout` already bounds churn
   per row. Add an explicit trigger entry point (the unit test in step 2
   calls it directly to avoid sleeping for the loop period).

```python
# backend/cubeplex/sandbox/cleanup.py
        try:
            await manager.reconcile_transients(claim_timeout=60)
            await manager.pause_idle()
            await manager.reap_paused()
        except Exception as e:
            logger.error("Error in sandbox cleanup loop: {}", e)
```

6. Re-run; confirm green:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_reconciler.py \
  tests/e2e/test_sandbox_reconciler.py -q
# EXPECTED: all passed
```

7. Commit:

```bash
git commit -m "feat(sandbox): reconciler for stuck pausing/resuming rows (OQ-3)"
```

---

## Task 6 — In-use lease around operations + cleanup-loop wiring

**Files**
- Modify: `backend/cubeplex/sandbox/lazy.py` (renew lease around every op via
  the manager, alongside the existing `touch`; release in `finally`)
- Modify: `backend/cubeplex/api/routes/v1/ws_browser.py` (`get_live_view` calls
  `manager.get_or_create` + `manager.touch` *directly*, bypassing the lazy
  lease path — it must renew the lease itself for the live-view session)
- Modify: `backend/cubeplex/sandbox/manager.py` (add `renew_lease` /
  `release_lease` wrappers that call the repo lease methods)
- Modify: `backend/cubeplex/sandbox/cleanup.py` (loop runs `pause_idle` +
  `reap_paused`)
- Test: `backend/tests/unit/test_sandbox_lease.py` (Create)

**Lease sizing (read first).** A single renewal sized at a fixed
`lease_seconds` (default 5 min per OQ-7) can be outlived by one long op (a
multi-minute `execute`, browser startup, large file transfer), letting the
idle reaper pause it mid-flight. Two rules to avoid that:
1. **Size the lease to the operation, not a constant.** Where an op has a
   known timeout (e.g. the connection/execute `request_timeout`), pass a lease
   at least as long as that timeout (plus a small margin) instead of the
   default `lease_seconds`. For unbounded streaming sessions (live view), renew
   periodically (heartbeat) rather than once.
2. **Cover every entry point, not just the lazy proxy.** The lease lives in
   `LazySandbox` (acquire on entering a long op, renew during, release on
   completion) — the boundary is the long-operation boundary (`execute`,
   browser start, file transfer), NOT agent-turn boundaries. Direct
   `manager.get_or_create` users (`ws_browser.get_live_view`) never pass
   through `LazySandbox._ensure_with_retry`, so they need their own
   renew (and, for a bounded request, `release_lease` in `finally`).

**Steps**

1. Failing unit tests: (a) a long op renews the lease so `in_use_until` is in
   the future, and `list_idle_to_pause_system` therefore excludes it;
   releasing clears the lease. (b) a lease sized to a long op timeout
   (`lease_seconds` > the op's wall-clock) is still in the future when the op
   ends, so the row is not a pause candidate mid-op. (c) the `get_live_view`
   direct-manager path renews its own lease (it does not go through the lazy
   proxy).

2. Run, confirm failure.

3. Manager wrappers:

```python
    async def renew_lease(
        self, sandbox_id, *, org_id, workspace_id, lease_seconds: int | None = None
    ) -> None:
        # lease_seconds lets callers size the lease to a known op timeout;
        # defaults to the configured lease_seconds (5 min, OQ-7) for short ops.
        window = lease_seconds if lease_seconds is not None else self._lease_seconds
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.acquire_in_use(record.id, window)

    async def release_lease(self, sandbox_id, *, org_id, workspace_id) -> None:
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.release_in_use(record.id)
```

4. In `LazySandbox._ensure_with_retry`, after the existing `touch`, also
   renew the lease — this is the chokepoint the lazy ops (`execute`,
   `upload`, `download`) pass through (spec OQ-7). The lease boundary is the
   long-operation boundary; the LazySandbox layer is where it lives. Size the
   lease to the op's timeout where one is known (so a single long op cannot
   outlive its lease), defaulting to `lease_seconds` (5 min) otherwise:

```python
        try:
            # Size to the op timeout when known so one long op can't be paused
            # mid-flight; falls back to the default lease_seconds (5 min).
            await self._manager.renew_lease(
                sandbox.id, org_id=self._org_id, workspace_id=self._workspace_id,
                lease_seconds=self._op_timeout_seconds,  # None -> default 5 min
            )
        except Exception:
            logger.exception("Lazy sandbox: lease renew failed (non-fatal)")
```

   (Lease expires naturally after the renewed window; a crashed holder can't
   pin the sandbox forever. For a bounded request that owns the sandbox for its
   duration, release in `finally` via `release_lease` rather than waiting for
   natural expiry.)

5. **Cover the direct-manager path.** `ws_browser.get_live_view` calls
   `manager.get_or_create` + `manager.touch` directly (lines ~57/66) and never
   touches `LazySandbox`, so it must renew the lease itself for the live-view
   session — and release it when the session ends. Add a `renew_lease`
   alongside the existing `touch`, and a `release_lease` in a `finally` for the
   bounded request. For a long-lived streaming session, renew on a heartbeat.

6. Wire both reapers into the loop:

```python
# backend/cubeplex/sandbox/cleanup.py
        try:
            await manager.pause_idle()
            await manager.reap_paused()
        except Exception as e:
            logger.error("Error in sandbox cleanup loop: {}", e)
```

   `pause_idle` internally falls back to `cleanup_expired` when
   `pause_on_idle` is off, so the loop covers both modes.

7. Re-run lease test; confirm green:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_lease.py -q
# EXPECTED: all passed
```

8. Commit:

```bash
git commit -m "feat(sandbox): in-use lease renewal + wire pause/reap into cleanup loop"
```

---

## Task 7 — E2E pause/resume round-trip against real OpenSandbox

**Files**
- Test: `backend/tests/e2e/test_sandbox_pause_resume.py` (Create,
  `pytestmark = pytest.mark.e2e`)

**Steps**

1. Write the E2E (requires a running OpenSandbox; mirror the connection
   setup in `tests/e2e/test_opensandbox.py`). Cover the spec's testing
   matrix:

   - **Round-trip + memory survival**: create via manager → write
     `/workspace/keep.txt` (PVC) and `/tmp/ephemeral.txt` (outside PVC) →
     `pause_idle()` (with `ttl_seconds=0`) → assert DB row `paused` and
     `provider.get_info().status.state == SandboxState.PAUSED` → reuse via
     `get_or_create` → assert both files readable and `execute("echo ok")`
     works. The `/tmp` file proves native pause preserves more than the PVC.
   - **Endpoint reconstruction**: after resume, `execute` succeeds (execd
     re-resolved); with the browser skill, `start_browser()` +
     `get_browser_endpoint()` returns a fresh URL.
   - **Idle auto-pause vs. active**: short `ttl_seconds`; an
     idle+unleased row pauses (not terminated); a row whose lease was just
     renewed is NOT selected by `list_idle_to_pause_system`.
   - **Paused-TTL reap**: short `paused_ttl`, `reap_paused()` →
     `terminated` + egress refs revoked.
   - **Capability gap**: a `LocalSandbox` / fake non-pausing driver →
     `pause_idle` kills (never `paused`); resume path falls back to create.

2. Run (gated):

```bash
cd backend && uv run pytest tests/e2e/test_sandbox_pause_resume.py -m e2e -q
# EXPECTED: passed (skipped only if OpenSandbox unavailable)
```

3. Commit:

```bash
git commit -m "test(sandbox): e2e pause/resume round-trip + idle/reap/capability"
```

---

## Task 8 — Concurrency unit tests (claim race + double-resume)

**Files**
- Test: `backend/tests/e2e/test_sandbox_pause_concurrency.py`
  (Create)

**Steps**

1. Tests (state-machine races, unit/integration — no real provider needed):
   - **`claim_pausing` race**: two concurrent `claim_pausing` on the same
     `running` row — exactly one returns `True`, the other `False`; the
     row never gets handed out for use while `pausing`.
   - **Double-resume guard**: two overlapping `_resume_record` calls on one
     `paused` row — `mark_resuming` succeeds once, so the provider resume runs
     exactly once. The loser does **not** create a new sandbox: it polls and,
     once the winner marks the row `running`, connects to that same
     `sandbox_id` (assert both calls return a handle to the *same* sandbox, and
     `OpenSandbox.connect_or_resume` is invoked only once). Cover the winner-fails
     case too: if the winner ends `failed`/`terminated`, the loser returns
     `None` so the caller may create a fresh one.

2. Run + confirm green:

```bash
cd backend && uv run pytest tests/e2e/test_sandbox_pause_concurrency.py -q
# EXPECTED: all passed
```

3. Commit:

```bash
git commit -m "test(sandbox): claim_pausing race + double-resume guard"
```

---

## Pre-PR sweep

After all tasks, run the changed-module suites together (full E2E reserved
for this sweep):

```bash
cd backend && uv run pytest tests/unit/test_sandbox_manager_pause.py \
  tests/unit/test_sandbox_pause_capability.py tests/unit/test_sandbox_lease.py \
  tests/unit/test_user_sandbox_model.py \
  tests/e2e/test_user_sandbox_repo_transitions.py \
  tests/e2e/test_sandbox_pause_concurrency.py -q
cd backend && uv run pytest tests/e2e/test_sandbox_pause_resume.py -m e2e -q
cd backend && uv run mypy cubeplex/sandbox cubeplex/models/user_sandbox.py \
  cubeplex/repositories/user_sandbox.py
# EXPECTED: all green; mypy clean
```
