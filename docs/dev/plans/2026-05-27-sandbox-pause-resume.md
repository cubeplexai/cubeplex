# Sandbox Pause/Resume Implementation Plan

> For agentic workers: execute tasks top-to-bottom. Each task is TDD —
> write the test first, watch it fail, implement, watch it pass. Stay on
> branch `feat/sandbox-pause-resume`; never switch to main or merge
> mid-execution. Read `.worktree.env` before running anything: this slot
> uses backend port **8058** and DB `cubebox_feat_sandbox_pause_resume`;
> tests auto-route to `cubebox_test_feat_sandbox_pause_resume`. Run tests
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
  the SDK `resume` is a classmethod that re-resolves execd/egress
  endpoints and rebuilds adapters. `Sandbox.resume_by_id(...)` is a driver
  classmethod; the manager discards the old handle, re-applies egress,
  health-probes, marks `running`.
- **Two reaper passes** in the existing cleanup loop:
  `pause_idle()` (pause-capable providers) and `reap_paused()` (hard-kill
  paused rows past `paused_ttl_seconds`). Non-capable providers fall back
  to `cleanup_expired()` kill.

## Tech Stack

FastAPI + async SQLModel/SQLAlchemy (Postgres), Alembic autogenerate,
`opensandbox` SDK (`Sandbox.pause()` / `Sandbox.resume(...)` classmethod /
`SandboxState` constants), pytest + pytest-asyncio (`-m e2e` against a real
OpenSandbox; unit tests for state-machine races). mypy strict, 100-char
lines.

---

## Task 1 — Add lifecycle columns to `UserSandbox`

**Files**
- Modify: `backend/cubebox/models/user_sandbox.py`
- Test: `backend/tests/unit/test_user_sandbox_model.py` (Create)

**Steps**

1. Write the failing test first:

```python
# backend/tests/unit/test_user_sandbox_model.py
from cubebox.models.user_sandbox import UserSandbox


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
    assert row.paused_ttl_seconds == 604800
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
    provider: str = Field(default="opensandbox", max_length=32)
    paused_at: datetime | None = Field(default=None)
    paused_ttl_seconds: int = Field(default=604800)  # 7 days
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
   with matching downgrade `drop_column`s. If the autogen diff is wrong
   (e.g. unrelated drift), fix the *model*, downgrade, delete the bad
   revision file, and re-autogenerate — do not patch the migration body.

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
- Modify: `backend/cubebox/repositories/user_sandbox.py`
- Test: `backend/tests/integration/test_user_sandbox_repo_transitions.py`
  (Create — uses the real per-slot test DB so the raw UPDATE is exercised)

**Steps**

1. Write failing integration tests covering: (a) `claim_pausing` flips a
   stale-idle unleased `running` row to `pausing` and returns `True`;
   (b) a second `claim_pausing` on the now-`pausing` row returns `False`
   (single-winner); (c) a row whose `in_use_until` is in the future is
   **not** claimed; (d) a row whose `last_activity_at` is fresh is **not**
   claimed; (e) `get_active_by_user` ignores `pausing`/`paused`;
   (f) `get_resumable_by_user` returns a `paused` row but never
   `pausing`/`resuming`; (g) `mark_paused`/`mark_resuming`/`mark_running`
   reject illegal prior states.

```python
# backend/tests/integration/test_user_sandbox_repo_transitions.py
from datetime import UTC, datetime, timedelta

import pytest

from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.user_sandbox import UserSandboxRepository

pytestmark = pytest.mark.integration


async def _mk(repo, session, *, status="running", idle_secs=10, lease=None):
    row = UserSandbox(
        user_id="u_1", sandbox_id=f"sbx_{datetime.now(UTC).timestamp()}",
        image="img", status=status, ttl_seconds=1,
        last_activity_at=datetime.now(UTC) - timedelta(seconds=idle_secs),
        in_use_until=lease,
    )
    row = await repo.add(row)
    await session.commit()
    return row


async def test_claim_pausing_single_winner(db_session, org_ctx):
    repo = UserSandboxRepository(db_session, **org_ctx)
    row = await _mk(repo, db_session)
    assert await repo.claim_pausing(row.id) is True
    assert await repo.claim_pausing(row.id) is False  # already pausing


async def test_claim_pausing_skips_leased(db_session, org_ctx):
    repo = UserSandboxRepository(db_session, **org_ctx)
    lease = datetime.now(UTC) + timedelta(minutes=5)
    row = await _mk(repo, db_session, lease=lease)
    assert await repo.claim_pausing(row.id) is False


async def test_claim_pausing_skips_fresh_activity(db_session, org_ctx):
    repo = UserSandboxRepository(db_session, **org_ctx)
    row = await _mk(repo, db_session, idle_secs=0)
    assert await repo.claim_pausing(row.id) is False


async def test_get_active_ignores_paused(db_session, org_ctx):
    repo = UserSandboxRepository(db_session, **org_ctx)
    await _mk(repo, db_session, status="paused")
    assert await repo.get_active_by_user("u_1") is None
```

   (Reuse whatever `db_session` / scoped-context fixtures
   `tests/integration/` already provides; mirror an existing repo
   integration test's fixture imports.)

2. Run, confirm failure:

```bash
cd backend && uv run pytest tests/integration/test_user_sandbox_repo_transitions.py -q
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

    async def acquire_in_use(self, record_id: str, lease_window: int) -> None:
        record = await self.get(record_id)
        if record:
            record.in_use_until = datetime.now(UTC) + timedelta(seconds=lease_window)
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
cd backend && uv run pytest tests/integration/test_user_sandbox_repo_transitions.py -q
# EXPECTED: all passed
```

6. Commit:

```bash
git commit -m "feat(sandbox): repo transitions + atomic claim_pausing"
```

---

## Task 4 — Provider capability methods (`supports_pause` / `pause` / `resume_by_id`)

**Files**
- Modify: `backend/cubebox/sandbox/base.py`
- Modify: `backend/cubebox/sandbox/opensandbox.py`
- Modify: `backend/cubebox/sandbox/local.py`
- Modify: `backend/cubebox/sandbox/lazy.py`
- Test: `backend/tests/unit/test_sandbox_pause_capability.py` (Create)

**Steps**

1. Failing unit test — capability defaults + LocalSandbox no-op + base
   `pause` raising:

```python
# backend/tests/unit/test_sandbox_pause_capability.py
import pytest

from cubebox.sandbox.base import Sandbox
from cubebox.sandbox.local import LocalSandbox


def test_local_sandbox_does_not_support_pause():
    sb = LocalSandbox()
    assert sb.supports_pause() is False


@pytest.mark.asyncio
async def test_base_pause_raises_not_implemented():
    sb = LocalSandbox()
    with pytest.raises(NotImplementedError):
        await sb.pause()


@pytest.mark.asyncio
async def test_resume_by_id_default_raises():
    with pytest.raises(NotImplementedError):
        await Sandbox.resume_by_id("sbx_x")
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
    async def resume_by_id(cls, sandbox_id: str, **kwargs: object) -> "Sandbox":
        """Resume a paused sandbox by id, returning a fresh handle with
        re-resolved endpoints. Override in capable drivers."""
        raise NotImplementedError("resume is not supported by this sandbox backend")
```

   Add `from __future__ import annotations` is already present; the
   `"Sandbox"` forward ref is fine.

4. OpenSandbox driver — implement all three. `resume_by_id` delegates to
   the SDK classmethod and wraps the result in `OpenSandbox`:

```python
# backend/cubebox/sandbox/opensandbox.py
from datetime import timedelta
from opensandbox.config import ConnectionConfig

    def supports_pause(self) -> bool:
        return True

    async def pause(self) -> None:
        with _as_sandbox_error():
            await self._sandbox.pause()

    @classmethod
    async def resume_by_id(
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
git commit -m "feat(sandbox): supports_pause/pause/resume_by_id on Sandbox + drivers"
```

---

## Task 5 — Manager: resume-on-reuse, `pause_idle`, `reap_paused` + config knobs

**Files**
- Modify: `backend/cubebox/sandbox/manager.py`
- Modify: `backend/config.yaml` (or the active config template — add the
  three `sandbox.*` knobs with defaults)
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
        self._pause_on_idle: bool = config.get("sandbox.pause_on_idle", True)
        self._paused_ttl: int = config.get("sandbox.paused_ttl", 604800)
        self._resume_timeout: int = config.get("sandbox.resume_timeout", 30)
        self._lease_window: int = config.get("sandbox.lease_window", 300)
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
                # resume failed -> fall through to create-new (record already
                # marked failed inside _resume_record)
                record = None

            if record:  # running -> existing connect + health-check path (unchanged)
                ...
```

   New helper:

```python
    async def _resume_record(self, session, repo, record, conn_config, *,
                             org_id, workspace_id, user_id) -> "OpenSandbox | None":
        if not await repo.mark_resuming(record.id):
            return None  # lost a race; let caller re-fetch / create
        try:
            backend = await OpenSandbox.resume_by_id(
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
```

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
                    await OpenSandbox(sandbox=raw, workdir=self._workdir).pause()
                    await scoped.mark_paused(record.id, paused_at=datetime.now(UTC))
                    logger.info("Paused idle sandbox {}", record.sandbox_id)
                except Exception as exc:
                    logger.warning("Pause failed for {}: {}; falling back to kill",
                                   record.sandbox_id, exc)
                    await scoped.mark_running(record.id)  # revert pausing -> running
                    await self._kill_record(session, scoped, record, conn_config)

    async def reap_paused(self) -> None:
        """Hard-kill paused rows past paused_ttl, bounding stored state."""
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

## Task 6 — In-use lease around operations + cleanup-loop wiring

**Files**
- Modify: `backend/cubebox/sandbox/lazy.py` (renew lease on every op via the
  manager, alongside the existing `touch`)
- Modify: `backend/cubebox/sandbox/manager.py` (add `renew_lease` /
  `release_lease` wrappers that call the repo lease methods)
- Modify: `backend/cubebox/sandbox/cleanup.py` (loop runs `pause_idle` +
  `reap_paused`)
- Test: `backend/tests/unit/test_sandbox_lease.py` (Create)

**Steps**

1. Failing unit test: a long op renews the lease so `in_use_until` is in the
   future, and `list_idle_to_pause_system` therefore excludes it; releasing
   clears the lease.

2. Run, confirm failure.

3. Manager wrappers:

```python
    async def renew_lease(self, sandbox_id, *, org_id, workspace_id) -> None:
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.acquire_in_use(record.id, self._lease_window)

    async def release_lease(self, sandbox_id, *, org_id, workspace_id) -> None:
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.release_in_use(record.id)
```

4. In `LazySandbox._ensure_with_retry`, after the existing `touch`, also
   renew the lease — this is the single chokepoint every long op
   (`execute`, browser startup, file transfer) passes through, so the
   renewal point sees every op (spec open-question 7):

```python
        try:
            await self._manager.renew_lease(
                sandbox.id, org_id=self._org_id, workspace_id=self._workspace_id
            )
        except Exception:
            logger.exception("Lazy sandbox: lease renew failed (non-fatal)")
```

   (Lease expires naturally after `lease_window`; a crashed holder can't pin
   the sandbox forever. No explicit release needed for v1 — natural expiry
   plus the next op's renewal is sufficient; `release_lease` is exposed for
   the request-end path if wired later.)

5. Wire both reapers into the loop:

```python
# backend/cubebox/sandbox/cleanup.py
        try:
            await manager.pause_idle()
            await manager.reap_paused()
        except Exception as e:
            logger.error("Error in sandbox cleanup loop: {}", e)
```

   `pause_idle` internally falls back to `cleanup_expired` when
   `pause_on_idle` is off, so the loop covers both modes.

6. Re-run lease test; confirm green:

```bash
cd backend && uv run pytest tests/unit/test_sandbox_lease.py -q
# EXPECTED: all passed
```

7. Commit:

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
- Test: `backend/tests/integration/test_sandbox_pause_concurrency.py`
  (Create)

**Steps**

1. Tests (state-machine races, unit/integration — no real provider needed):
   - **`claim_pausing` race**: two concurrent `claim_pausing` on the same
     `running` row — exactly one returns `True`, the other `False`; the
     row never gets handed out for use while `pausing`.
   - **Double-resume guard**: two overlapping `_resume_record` calls on one
     `paused` row — `mark_resuming` succeeds once; the loser returns `None`
     (re-fetch/create), so resume runs exactly once.

2. Run + confirm green:

```bash
cd backend && uv run pytest tests/integration/test_sandbox_pause_concurrency.py -q
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
  tests/integration/test_user_sandbox_repo_transitions.py \
  tests/integration/test_sandbox_pause_concurrency.py -q
cd backend && uv run pytest tests/e2e/test_sandbox_pause_resume.py -m e2e -q
cd backend && uv run mypy cubebox/sandbox cubebox/models/user_sandbox.py \
  cubebox/repositories/user_sandbox.py
# EXPECTED: all green; mypy clean
```
