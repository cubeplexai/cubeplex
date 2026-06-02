# Durable HITL: CheckpointedChannel + auto-detach + respond path — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 180-second HITL timeout with a durable, cross-instance pause/resume mechanism. Any backend worker can answer a paused conversation; the pending state survives worker crashes; the user can take as long as they want.

**Architecture:** Switch the per-run HITL channel from `InMemoryChannel` to cubepi's `CheckpointedChannel` (pending persisted to `cubepi_threads.pending_request`). Hook the `_on_event` agent listener to call `agent.detach()` whenever a `HitlRequestEvent` fires, so the worker releases as soon as the agent enters pending. Add a `resume_run_with_answer` entry point that reuses the original `run_id`, gated by a Redis `claim_resume` CAS to prevent duplicate/conflicting resumes. The frontend stays unchanged for the SSE-replay happy path; new `pending_hitl` in conversation status covers the cold-start fallback.

**Tech Stack:** Backend: Python 3.13, FastAPI, cubepi agent runtime, Postgres (asyncpg), Redis (Lua scripts), pytest+pytest-asyncio. Frontend: Next.js + React 19, Zustand store, Vitest, Playwright.

**Spec:** [docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md](../specs/2026-06-02-hitl-checkpointed-respond-design.md)

**Worktree:** `/home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond` (slot 52; API `127.0.0.1:8052`, web `127.0.0.1:3052`, DB `cubebox_feat_hitl_checkpointed_respond` / `cubebox_test_feat_hitl_checkpointed_respond`). Always `cat .worktree.env` first; all backend commands run from `backend/` and tests respect `CUBEBOX_DATABASE__NAME` automatically via `tests/conftest.py`.

---

## Upstream prerequisite (cubepi) + coordinated cubebox migration

Per CLAUDE.md / user memory: "cubepi 是自研组件，上游优先". The plan needs a small cubepi change before anything else, because cubebox cannot recover the `run_id` of a paused conversation across worker death without it (the existing `cubepi_threads.pending_request` JSONB has no field that ties the pending back to a run_id; `AskRequest` has no `details` to smuggle it through; the spec/plan's `pending_hitl.run_id` requirement is otherwise unmeetable).

**Two coordinated PRs — cubepi first, then cubebox.**

### Step A: Cubepi PR (lands first)

**Tracing surface (`cubepi/hitl/_trace.py`):**

- `hitl_span(kind, **attrs)` already stamps `hitl.question_id`, `hitl.tool_name`, `hitl.tool_call_id`, `hitl.timeout_seconds`, `hitl.from_resume`, `hitl.outcome`, `hitl.duration_seconds` (see `cubepi/hitl/channel.py:181-291`). After the channel gains `run_id`, extend `_await_answer` to pass `run_id=self._run_id` into `hitl_span` so traces carry it as `hitl.run_id`. Makes paused/resumed conversations groupable in trace storage by run_id.
- Add `hitl.detached: bool` attribute on the span's finally block — set when the outcome resolved via `HitlDetached` exception (vs `answer`/`cancelled`/`timed_out`). Lets traces distinguish "auto-detached for durable resume" from "real cancel/timeout".

**API surface changes** (apply to ALL checkpointers — PostgresCheckpointer, MySQLCheckpointer, SQLiteCheckpointer, MemoryCheckpointer — for Protocol parity; cubebox only uses Postgres, but cubepi's own tests + third-party users exercise the others):

- **`save_pending_request(thread_id, request, run_id=None)`** gains an optional `run_id` keyword. When non-None, writes `pending_request` AND `run_id` in ONE atomic statement (UPDATE for SQL backends; single dict assignment for Memory).
- **`load_pending_request(thread_id)` signature is UNCHANGED** — still returns `HitlRequest | None`. Keeps `Agent.load_pending_hitl_request` callers source-compatible.
- NEW `async def load_pending_run_id(thread_id: str) -> str | None` on all checkpointers — separate read of just the new column/slot.
- **No separate `set_pending_run_id` method.** A two-step write (pending now, run_id later) leaves a crash window where pending exists with `run_id=NULL`. Folding run_id into `save_pending_request` removes the window entirely.
- **`CheckpointedChannel.__init__(..., run_id: str | None = None)`** gains a `run_id` parameter. The channel passes it to every `save_pending_request` call from `_on_pending_set`. cubebox constructs the channel inside `_build_agent_for_conversation` where run_id is known.

**Per-backend storage changes:**

| Backend | Storage | Schema change |
|---|---|---|
| `PostgresCheckpointer` | `cubepi_threads.run_id TEXT NULL` | Cubebox-owned alembic revision (Step B). `EXPECTED_SCHEMA_VERSION` in `cubepi/checkpointer/postgres/models.py` bumps 2 → 3. |
| `MySQLCheckpointer` | `cubepi_threads.run_id VARCHAR(64) NULL` | Host-owned alembic (per cubepi/checkpointer/mysql/README.md — cubepi ships `cubepi_metadata` + `write_schema_version_op()` helpers; downstream hosts wire them into their own alembic chain). `EXPECTED_SCHEMA_VERSION` in `cubepi/checkpointer/mysql/models.py` bumps 2 → 3. cubebox doesn't ship MySQL but cubepi's own tests do — must be kept green. |
| `SQLiteCheckpointer` | `thread_pending_request.run_id TEXT NULL` (the pending lives in its own table, not `cubepi_threads`) | `__aenter__` does DDL inline (`CREATE TABLE IF NOT EXISTS thread_pending_request (...)`). Update the CREATE statement AND add a one-shot migration block (`PRAGMA table_info(thread_pending_request)` → `ALTER TABLE ... ADD COLUMN run_id TEXT`) for existing DBs. No schema_version concept — SQLite checkpointer doesn't gate on a version row. |
| `MemoryCheckpointer` | sibling dict `self._pending_run_id: dict[str, str]` keyed by thread_id | No DDL — just initialize in `__init__` and clear in `aclose()` / when pending is cleared. |

**Other prerequisites:**

- **Bump `EXPECTED_SCHEMA_VERSION` to 3** in BOTH `cubepi/checkpointer/postgres/models.py` AND `cubepi/checkpointer/mysql/models.py` (per-backend constants today — verified). `__aenter__` reads the `cubepi_schema_version` row on connect; on v2-database / v3-cubepi mismatch the policy is "refuse with a clear error message pointing at `alembic upgrade head`". (SQLite has no version check; Memory has no DDL.) Document the policy in the cubepi CHANGELOG so cubebox knows the cubebox-side alembic (Step B) must set the version itself.
- **Test coverage**: cubepi's existing checkpointer test matrix already runs `save_pending_request` + `load_pending_request` round-trips against all four backends. Extend those to:
  - `save_pending_request(req, run_id="r1")` then `load_pending_run_id() == "r1"`.
  - `save_pending_request(req)` (no kwarg, legacy compat) then `load_pending_run_id() is None`.
  - Save with run_id, then `save_pending_request(None)` clears both pending and run_id.

- Push, get merged, tag a new cubepi rev.

### Step B: Cubebox migration + pin bump (lands BEFORE any task in this plan)

cubebox owns `cubepi_threads` via its own alembic chain (see existing revision `fdcc495b3704_cubepi_v1_to_v2_pending_request.py`). The pin bump without a coordinated cubebox migration leaves existing databases on v2 — `PostgresCheckpointer.__aenter__` will hard-fail at startup until the column lands.

- New alembic revision in `backend/alembic/versions/` (e.g. `<hash>_cubepi_v2_to_v3_pending_run_id.py`):

  ```python
  def upgrade():
      op.add_column(
          "cubepi_threads",
          sa.Column("run_id", sa.Text(), nullable=True, server_default=sa.text("NULL")),
      )
      # Bump the cubepi schema version row. PostgresCheckpointer.__aenter__
      # at cubepi/checkpointer/postgres/checkpointer.py:91 reads
      # `SELECT version FROM cubepi_schema_version LIMIT 1` and refuses
      # to start when it doesn't match EXPECTED_SCHEMA_VERSION. Without
      # this UPDATE the new cubepi pin will hard-fail on every worker start.
      op.execute("UPDATE cubepi_schema_version SET version = 3")

  def downgrade():
      op.execute("UPDATE cubepi_schema_version SET version = 2")
      op.drop_column("cubepi_threads", "run_id")
  ```

- Bump cubepi pin in `cubebox/uv.lock` (NOT `pyproject.toml` — per CLAUDE.md memory "cubepi is a pinned git dep" via uv.lock rev).

- Verify the deploy order: **migration must run before any worker picks up the new pin**. Standard cubebox deploy is migration-first via `alembic upgrade head` (see `backend/Makefile` / startup script); confirm this is the case before rolling out.

The rest of the plan assumes both steps are live. `cp.load_pending_request(cid)` still returns `HitlRequest | None` (unchanged). To get run_id, call `await cp.load_pending_run_id(cid)` separately — used by the answer routes' run_id fallback, the bootstrap pending_hitl serializer, and cancel_paused_run. The write side stays atomic via `CheckpointedChannel` carrying run_id into `save_pending_request`; cubebox never makes a separate run_id write, so no crash window exists between the two columns.

---

## File Structure

**Backend — modify**

- `backend/cubebox/streams/run_events.py` — add `"paused_hitl"` to the status state machine; new `claim_resume` Lua + Python wrapper; teach `start_run` / `_CLAIM_ACTIVE_LUA` to reject when existing meta is `paused_hitl` AND when DB pending is non-null; teach the stale-run sweeper to skip `paused_hitl`; **extend `_FORCE_CLAIM_STALE_LUA` to also protect `paused_hitl` rows** (today it force-claims anything that isn't `running` — would silently overwrite a paused conversation).
- `backend/cubebox/schedules/dispatch.py` — `ConversationBusyError` detection must distinguish "running, retry" from "paused HITL, do not retry" so the scheduled-task poller doesn't burn its retry budget on a conversation the user has to unblock.
- `backend/cubebox/streams/run_manager.py` — move `async with init_checkpointer() as cp:` upward to wrap section 6+; swap `InMemoryChannel` → `CheckpointedChannel(checkpointer=cp, thread_id=conversation_id, run_id=run_id, default_timeout=None)`; extend `_on_event` with the auto-detach hook; add `_classify_terminal_status` helper; extract `_build_agent_for_conversation()` factory; add `_run_cubepi_respond_path` + `_execute_respond_run`; add `resume_run_with_answer` + `cancel_paused_run`; remove `dispatch_ask_user_answer` / `dispatch_hitl_answer` and the `ask_user_answer`/`hitl_answer` arms of `_handle_control`. (run_id durability is atomic via the channel; no separate set_pending_run_id call needed.) **Existing `_trace_meta` dict (around line 1807) gains `run_id` and `turn_kind="prompt"` keys** so the `invoke_agent` span groups paused/resumed turns by run_id alongside the new respond/abort paths.
- `backend/cubebox/middleware/sandbox.py` — drop `timeout=180.0` from `channel.approve(...)`.
- `backend/cubebox/api/routes/v1/conversations.py` — answer routes call `resume_run_with_answer`; cancel route detects paused state and routes through `cancel_paused_run`; **steer / cancel_steer / cancel / answer routes must all replace the `status != "running"` precheck** (lines 1051, 1091, 1127, 1165, 1205) with paused-state-aware branches — current behavior returns `no_active_run` for `paused_hitl`, which silently breaks every user action on a paused conversation; conversation bootstrap/status response includes `pending_hitl`.
- `backend/cubebox/api/schemas/conversations.py` (or wherever bootstrap response lives) — `pending_hitl` payload schema (TS-mirrored union from spec §7).
- `backend/cubebox/agents/schemas.py` — extend `SandboxConfirmResolvedEvent` doc + accept `"policy_overridden"` as a `decision` value (no code change; doc only).

**Backend — create**

- `backend/cubebox/streams/hitl_resume.py` — small module owning the `claim_resume` Lua + Python wrapper + the dangling-pending cleanup helpers (keeps `run_manager.py` from growing further).

**Backend — tests**

- `backend/tests/unit/test_hitl_claim_resume.py` (NEW) — Lua CAS semantics.
- `backend/tests/unit/test_run_manager_classify_terminal_status.py` (NEW) — `_classify_terminal_status` truth table.
- `backend/tests/unit/test_run_manager_auto_detach.py` (NEW) — `_on_event` schedules `agent.detach()` on `HitlRequestEvent`.
- `backend/tests/unit/test_sandbox_confirm_gate.py` (MODIFY) — drop `timeout=180.0` assertion.
- `backend/tests/unit/test_run_manager_resume_run_with_answer.py` (NEW) — happy + 404 + 409 + dangling cleanup paths (mocked checkpointer + Redis).
- `backend/tests/e2e/test_hitl_pause_resume.py` (NEW) — happy path; long-pause TTL recovery; cross-worker resume; policy change mid-pause; two-tab race.

**Frontend — modify**

- `frontend/packages/core/src/types/events.ts` — extend `decision` union with `"policy_overridden"`; bootstrap response gains `pending_hitl`.
- `frontend/packages/core/src/stores/messageStore.ts` — `applyStreamEvent` accepts `"policy_overridden"`; `loadMessages` reads `bootstrap.pending_hitl` to seed `pendingConfirmMap` / `pendingAsk` on cold-start; new `submitAskUserAnswer` / `submitSandboxConfirm` handlers gracefully accept 409 `resume_in_flight`.
- `frontend/packages/web/components/chat/SandboxConfirmCard.tsx` / `AskUserCard.tsx` — render an inline "Skipped — org sandbox policy changed" note when the resolution carries `decision: "policy_overridden"`.
- `frontend/packages/web/components/chat/Composer.tsx` (or wherever the message composer lives) — disable when `pendingConfirmMap` or `pendingAsk` is non-empty; tooltip "Answer the pending question above first."

**Frontend — tests**

- `frontend/packages/core/__tests__/stores/messageStore.policyOverridden.test.ts` (NEW).
- `frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts` (NEW).
- Composer disabled-state test (location follows existing composer test pattern).

---

## Task 1: Add `paused_hitl` to the run-status state machine

**Files:**
- Modify: `backend/cubebox/streams/run_events.py`
- Test: `backend/tests/unit/test_run_events_paused_hitl.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_events_paused_hitl.py
import pytest
import pytest_asyncio
from cubebox.streams.run_events import (
    create_run, get_active_run, update_run_meta, is_stale_meta,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_client():
    import redis.asyncio as redis
    from cubebox.config import config
    c = redis.from_url(config.get("redis.url", "redis://localhost:6379"))
    yield c
    await c.close()


async def test_paused_hitl_status_round_trips(redis_client):
    prefix = "test_paused_hitl"
    await create_run(
        redis_client, prefix=prefix, run_id="r1", conversation_id="c1",
        status="running", started_at="2026-06-02T00:00:00Z", user_message="hi",
        ttl_seconds=60,
    )
    await update_run_meta(
        redis_client, prefix=prefix, conversation_id="c1", run_id="r1",
        status="paused_hitl",
    )
    meta = await get_active_run(redis_client, prefix=prefix, conversation_id="c1")
    assert meta is not None
    assert meta.status == "paused_hitl"


async def test_paused_hitl_is_not_stale(redis_client):
    """paused_hitl rows have no freshness expectation; sweeper must skip them."""
    from cubebox.streams.run_events import RunMeta
    meta = RunMeta(
        run_id="r1", conversation_id="c1", status="paused_hitl",
        started_at="2026-06-02T00:00:00Z",
        user_message="hi",
        first_event_id=None,
        last_event_id=None,
        last_event_at="2020-01-01T00:00:00Z",  # ancient
    )
    assert is_stale_meta(meta, threshold_seconds=10) is False


async def test_force_claim_stale_protects_paused_hitl(redis_client):
    """_FORCE_CLAIM_STALE_LUA force-claims anything != 'running' today. After
    paused_hitl is added, the script must protect it too — otherwise create_run
    will silently overwrite a paused conversation when _CLAIM_ACTIVE_LUA falls back."""
    prefix = "test_force_claim_paused"
    await create_run(
        redis_client, prefix=prefix, run_id="r1", conversation_id="c1",
        status="running", started_at="t0", user_message="hi", ttl_seconds=60,
    )
    await update_run_meta(
        redis_client, prefix=prefix, conversation_id="c1", run_id="r1",
        status="paused_hitl",
    )
    # Concurrent create_run attempt for a new run_id should NOT force-claim
    # over the paused run.
    with pytest.raises(RuntimeError):
        await create_run(
            redis_client, prefix=prefix, run_id="r2", conversation_id="c1",
            status="running", started_at="t1", user_message="next", ttl_seconds=60,
        )
    # The original paused run is still intact.
    meta = await get_active_run(redis_client, prefix=prefix, conversation_id="c1")
    assert meta is not None and meta.run_id == "r1" and meta.status == "paused_hitl"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond/backend
uv run pytest tests/unit/test_run_events_paused_hitl.py -v
```
Expected: both tests FAIL (`paused_hitl` not a valid status; `is_stale_meta` doesn't special-case it).

- [ ] **Step 3: Implement**

In `backend/cubebox/streams/run_events.py`:

```python
# Module-level constant near the existing _APPEND_EVENT_LUA comment:
RUN_STATUSES = ("running", "paused_hitl", "completed", "cancelled", "errored", "stale")
```

Verify `is_stale_meta` (around line 509) already returns False for any
status other than "running" — if so, no behavioral change is needed for
the freshness check (the new test asserts existing behavior holds for
the new status). If it doesn't, add the early-return.

**Extend `_FORCE_CLAIM_STALE_LUA` (lines 91-110) to also protect `paused_hitl`:**

```lua
-- OLD (line 98-100):
if status == 'running' then
  return 0
end

-- NEW:
if status == 'running' or status == 'paused_hitl' then
  return 0
end
```

This is the most load-bearing line in Task 1: without it, a stale-Redis-but-paused-DB conversation can be silently overwritten by a concurrent `start_run` falling back to force-claim.

If `update_run_meta` validates `status` against an explicit allowlist, add `"paused_hitl"` to it. If it accepts any string, no change.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_run_events_paused_hitl.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_events.py backend/tests/unit/test_run_events_paused_hitl.py
git commit -m "feat(runs): add paused_hitl status to run state machine"
```

---

## Task 2: `claim_resume` Lua + Python wrapper

**Files:**
- Create: `backend/cubebox/streams/hitl_resume.py`
- Test: `backend/tests/unit/test_hitl_claim_resume.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_hitl_claim_resume.py
import pytest
import pytest_asyncio
from cubebox.streams.run_events import create_run, update_run_meta
from cubebox.streams.hitl_resume import claim_resume, ClaimResumeOutcome

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_client():
    import redis.asyncio as redis
    from cubebox.config import config
    c = redis.from_url(config.get("redis.url", "redis://localhost:6379"))
    yield c
    await c.close()


async def test_claim_resume_from_paused_hitl(redis_client):
    prefix = "test_claim_paused"
    await create_run(
        redis_client, prefix=prefix, run_id="r1", conversation_id="c1",
        status="running", started_at="t0", user_message="hi", ttl_seconds=60,
    )
    await update_run_meta(
        redis_client, prefix=prefix, conversation_id="c1", run_id="r1",
        status="paused_hitl",
    )
    result = await claim_resume(
        redis_client, prefix=prefix,
        conversation_id="c1", expected_run_id="r1", ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.OK
    assert result.claim_token  # non-empty


async def test_claim_resume_rejects_running(redis_client):
    prefix = "test_claim_running"
    await create_run(
        redis_client, prefix=prefix, run_id="r1", conversation_id="c1",
        status="running", started_at="t0", user_message="hi", ttl_seconds=60,
    )
    result = await claim_resume(
        redis_client, prefix=prefix,
        conversation_id="c1", expected_run_id="r1", ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.ALREADY_RUNNING


async def test_claim_resume_conflict_when_active_moved(redis_client):
    prefix = "test_claim_conflict"
    await create_run(
        redis_client, prefix=prefix, run_id="r1", conversation_id="c1",
        status="running", started_at="t0", user_message="hi", ttl_seconds=60,
    )
    # active key now points at r1; client requests r0
    result = await claim_resume(
        redis_client, prefix=prefix,
        conversation_id="c1", expected_run_id="r0", ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.CONFLICT


async def test_claim_resume_rebuilds_when_meta_expired(redis_client):
    """Long pause case: active key + meta both aged out, but DB pending still
    exists so caller knows the run_id + conversation_id + started_at. Claim
    must rebuild a COMPLETE meta hash — _meta_from_hash bracket-accesses
    run_id, conversation_id, status, started_at."""
    prefix = "test_claim_rebuild"
    # active key + meta absent (simulate post-TTL)
    result = await claim_resume(
        redis_client, prefix=prefix,
        conversation_id="c1", expected_run_id="r1",
        started_at="2026-06-02T00:00:00Z",  # caller must provide for rebuild
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.OK
    # The rebuilt meta is well-formed and readable via get_active_run.
    meta = await get_active_run(redis_client, prefix=prefix, conversation_id="c1")
    assert meta is not None
    assert meta.run_id == "r1"
    assert meta.conversation_id == "c1"
    assert meta.status == "running"
    assert meta.started_at == "2026-06-02T00:00:00Z"
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_hitl_claim_resume.py -v
```
Expected: import error / module-not-found.

- [ ] **Step 3: Implement**

```python
# backend/cubebox/streams/hitl_resume.py
"""Single-flight resume claim for paused HITL conversations.

See docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md §5.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from cubebox.streams.run_events import _active_run_key, _run_meta_key


class ClaimResumeOutcome(str, enum.Enum):
    OK = "ok"
    ALREADY_RUNNING = "already_running"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ClaimResumeResult:
    outcome: ClaimResumeOutcome
    claim_token: str | None


# KEYS[1] = active_key, KEYS[2] = meta_key
# ARGV[1] = expected_run_id, ARGV[2] = new_claim_token,
# ARGV[3] = ttl_seconds, ARGV[4] = last_event_at_iso,
# ARGV[5] = conversation_id, ARGV[6] = started_at_iso
#
# Returns: "ok" | "already_running" | "conflict"
#
# CRITICAL: when meta does not exist (long-pause TTL recovery), we MUST
# rebuild it with all the fields _meta_from_hash requires (run_id,
# conversation_id, status, started_at). Otherwise the next get_active_run
# crashes with KeyError on the half-built hash.
_CLAIM_RESUME_LUA = """
local current = redis.call('GET', KEYS[1])
if current and current ~= ARGV[1] then
  return 'conflict'
end
local meta_exists = redis.call('EXISTS', KEYS[2]) == 1
if meta_exists then
  local status = redis.call('HGET', KEYS[2], 'status')
  if status == 'running' then
    return 'already_running'
  end
  if status ~= 'paused_hitl' and status ~= 'stale' then
    return 'conflict'
  end
  redis.call('HSET', KEYS[2],
    'status', 'running',
    'claim_token', ARGV[2],
    'last_event_at', ARGV[4]
  )
else
  -- Rebuild path: meta TTL aged out. Write ALL required RunMeta fields.
  redis.call('HSET', KEYS[2],
    'run_id', ARGV[1],
    'conversation_id', ARGV[5],
    'status', 'running',
    'started_at', ARGV[6],
    'claim_token', ARGV[2],
    'last_event_at', ARGV[4]
  )
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
return 'ok'
"""


async def claim_resume(
    redis: Redis,
    *,
    prefix: str,
    conversation_id: str,
    expected_run_id: str,
    started_at: str,           # ISO8601; required for the rebuild branch
    ttl_seconds: int,
) -> ClaimResumeResult:
    """Atomically claim a paused/stale/missing active-run slot for resume.

    `started_at` is needed because the rebuild path (long pause beyond
    Redis TTL) has to re-populate the meta hash with all the fields
    `_meta_from_hash` requires. Callers get `started_at` from the
    `pending_hitl.requested_at` payload (which is itself derived from
    the DB pending). See spec §5 "Resume claim — single-flight guarantee".
    """
    from datetime import UTC, datetime

    new_token = uuid.uuid4().hex
    now_iso = datetime.now(UTC).isoformat()
    outcome = await redis.eval(
        _CLAIM_RESUME_LUA,
        2,
        _active_run_key(prefix, conversation_id),
        _run_meta_key(prefix, expected_run_id),
        expected_run_id,
        new_token,
        str(ttl_seconds),
        now_iso,
        conversation_id,
        started_at,
    )
    outcome_str = outcome.decode() if isinstance(outcome, bytes) else outcome
    return ClaimResumeResult(
        outcome=ClaimResumeOutcome(outcome_str),
        claim_token=new_token if outcome_str == "ok" else None,
    )
```

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_hitl_claim_resume.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/hitl_resume.py backend/tests/unit/test_hitl_claim_resume.py
git commit -m "feat(runs): add claim_resume single-flight Lua CAS for paused HITL"
```

---

## Task 3: Extend `start_run` to reject paused_hitl and consult DB pending

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (in `start_run`)
- Test: `backend/tests/unit/test_run_manager_start_run_paused.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_start_run_paused.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio


async def test_start_run_rejects_when_db_pending_non_null(monkeypatch):
    """Worker crashed mid-pause: Redis says 'stale' (or row missing) but
    DB still has pending. start_run must reject — the conversation is
    NOT done."""
    from cubebox.streams.run_manager import RunManager, RunContext

    rm = ... # build minimal RunManager (see existing tests for pattern)
    # Stub checkpointer.load_pending_request to return a non-null pending.
    # (load_pending_run_id is a separate call; not exercised in this test.)
    cp_mock = AsyncMock()
    _fake_pending = MagicMock()
    _fake_pending.question_id = "q1"
    cp_mock.load_pending_request = AsyncMock(return_value=_fake_pending)
    monkeypatch.setattr(
        "cubebox.streams.run_manager.init_checkpointer",
        lambda: _fake_cm(cp_mock),
    )
    # ... simulate no active row in Redis
    with pytest.raises(RuntimeError, match="paused"):
        await rm.start_run(
            conversation_id="c1", content="hi",
            ctx=RunContext(user_id="u", org_id="o", workspace_id="w"),
        )
```

(Note: this test will need a small `_fake_cm` async context-manager helper and a RunManager-builder fixture — pattern exists in other tests in this directory; copy from `test_run_manager_cubepi_dict_to_event.py` setup.)

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_start_run_paused.py -v
```
Expected: FAIL — start_run currently only checks Redis.

- [ ] **Step 3: Implement**

In `backend/cubebox/streams/run_manager.py` `start_run`, after the existing `get_active_run` conflict check:

```python
# Additional DB-pending guard: a worker crash between pending persist and
# Redis transition can leave DB pending while Redis appears unlocked.
# DB is authoritative for "is this conversation paused". See spec §4.
from cubebox.agents.checkpointer import init_checkpointer  # already imported
async with init_checkpointer() as _cp:
    _db_pending = await _cp.load_pending_request(conversation_id)
if _db_pending is not None:
    raise RuntimeError(
        f"Conversation {conversation_id} has a pending HITL request "
        f"(question_id={_db_pending.question_id}); "
        f"answer or cancel before starting a new turn"
    )
```

Also extend the existing `existing.status == "running"` check to include `paused_hitl`:

```python
if existing and existing.status in ("running", "paused_hitl"):
    raise RuntimeError(...)
```

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_run_manager_start_run_paused.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/unit/test_run_manager_start_run_paused.py
git commit -m "feat(runs): start_run rejects conversations with pending HITL"
```

---

## Task 4: Swap to CheckpointedChannel + remove 180-second timeout

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (section 6 channel construction; move `async with init_checkpointer()` up)
- Modify: `backend/cubebox/middleware/sandbox.py:446` (drop `timeout=180.0`)
- Test: `backend/tests/unit/test_sandbox_confirm_gate.py` (MODIFY — drop 180.0 assertion)

- [ ] **Step 1: Update the failing existing test + stub signature**

In `backend/tests/unit/test_sandbox_confirm_gate.py`:

(a) Around line 102, update the assertion:

```python
# OLD:
assert ch.calls[0]["timeout"] == 180.0
# NEW:
assert "timeout" not in ch.calls[0] or ch.calls[0]["timeout"] is None
```

(b) Find `class _StubChannel` (or `_Channel` in `test_sandbox_scoping.py`) and update its `approve` signature so `timeout` is optional:

```python
# OLD:
async def approve(self, *, tool_name, tool_call_id, args, details, timeout, signal=None):
# NEW:
async def approve(self, *, tool_name, tool_call_id, args, details, timeout=None, signal=None):
```

Without this, after step 3 removes `timeout=180.0` from production, every stub-based test raises `TypeError: missing 1 required keyword-only argument: 'timeout'` and fails for the wrong reason.

- [ ] **Step 2: Run to verify it FAILS**

```
uv run pytest tests/unit/test_sandbox_confirm_gate.py::test_confirm_approve_runs_tool -v
```
Expected: FAIL — code still passes 180.0.

- [ ] **Step 3: Implement — sandbox middleware**

In `backend/cubebox/middleware/sandbox.py:440-448`:

```python
# OLD:
answer = await self.channel.approve(
    tool_name="execute",
    tool_call_id=ctx.tool_call.id,
    args={"command": command},
    details={"matched_pattern": pattern, "command": command},
    timeout=180.0,
    signal=signal,
)
# NEW (drop the timeout line; the channel's default_timeout=None propagates):
answer = await self.channel.approve(
    tool_name="execute",
    tool_call_id=ctx.tool_call.id,
    args={"command": command},
    details={"matched_pattern": pattern, "command": command},
    signal=signal,
)
```

The `except HitlTimedOut` block stays — kept as defence even though no current caller passes a timeout.

- [ ] **Step 4: Implement — run_manager channel swap**

In `backend/cubebox/streams/run_manager.py` `_run_cubepi_path`:

(a) Move `async with init_checkpointer() as cp:` (currently around line 1691) up to BEFORE section 6 (currently around line 1527). Re-indent sections 6, 7, 8 and the agent run body under the new scope. The existing inner `async with init_checkpointer() as cp:` block goes away (its contents are now under the outer one).

(b) In section 6, replace:

```python
# OLD:
from cubepi.hitl import InMemoryChannel
sandbox_hitl_channel = InMemoryChannel(default_timeout=180.0)
```

with:

```python
# NEW: pass run_id so every save_pending_request from the channel
# writes pending + run_id in one atomic statement (no crash window).
from cubepi.hitl import CheckpointedChannel
sandbox_hitl_channel = CheckpointedChannel(
    checkpointer=cp,
    thread_id=conversation_id,
    run_id=run_id,
    default_timeout=None,
)
```

- [ ] **Step 5: Run targeted tests**

```
uv run pytest tests/unit/test_sandbox_confirm_gate.py -v
uv run pytest tests/unit/test_run_manager_cubepi_dict_to_event.py -v
```
Expected: all PASS. Confirm timeout assertion change holds.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/middleware/sandbox.py \
        backend/cubebox/streams/run_manager.py \
        backend/tests/unit/test_sandbox_confirm_gate.py
git commit -m "feat(hitl): switch to CheckpointedChannel and remove 180s timeout"
```

---

## Task 5: Auto-detach hook on `HitlRequestEvent`

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (extend `_on_event` listener)
- Test: `backend/tests/unit/test_run_manager_auto_detach.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_auto_detach.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio


async def test_on_event_schedules_detach_on_hitl_request():
    """The auto-detach hook must schedule agent.detach() exactly once when
    a HitlRequestEvent fires."""
    from cubepi.agent.types import HitlRequestEvent
    from cubepi.hitl.types import HitlRequest, ApproveRequest
    from cubebox.streams.run_manager import _build_auto_detach_listener

    agent = MagicMock()
    agent.detach = AsyncMock()
    listener = _build_auto_detach_listener(agent)

    req = HitlRequest(
        question_id="q1",
        thread_id="t1",
        payload=ApproveRequest(
            tool_name="execute", tool_call_id="tc1",
            args={"command": "ls"}, details={"matched_pattern": "ls *"},
        ),
        created_at=1700000000.0,
    )
    evt = HitlRequestEvent(request=req)
    listener(evt)
    # Allow the create_task to run
    await asyncio.sleep(0)
    agent.detach.assert_called_once()


async def test_on_event_does_not_detach_on_other_events():
    """Any event that's not a HitlRequestEvent must NOT trigger detach.
    We use a simple sentinel object since cubepi event types vary in
    construction shape (Message, etc.) and we only care about the
    isinstance check."""
    from cubebox.streams.run_manager import _build_auto_detach_listener

    agent = MagicMock()
    agent.detach = AsyncMock()
    listener = _build_auto_detach_listener(agent)
    # Pass any non-HitlRequestEvent object — isinstance check must reject it.
    listener(object())
    await asyncio.sleep(0)
    agent.detach.assert_not_called()
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_auto_detach.py -v
```
Expected: FAIL — `_build_auto_detach_listener` doesn't exist.

- [ ] **Step 3: Implement**

In `backend/cubebox/streams/run_manager.py`, add module-level helper above `_run_cubepi_path`:

```python
class _AutoDetachListener:
    """Schedules agent.detach() exactly once on HitlRequestEvent and
    exposes `.detached` so the terminal block in Task 6 can read whether
    this turn entered HITL (distinguishes 'real new pending' from 'stale
    pending leftover from a prior session')."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self.detached: bool = False

    def __call__(self, evt: Any, _signal: Any = None) -> None:
        from cubepi.agent.types import HitlRequestEvent
        if self.detached:
            return
        if isinstance(evt, HitlRequestEvent):
            self.detached = True
            asyncio.create_task(self._agent.detach())


def _build_auto_detach_listener(agent: Any) -> _AutoDetachListener:
    return _AutoDetachListener(agent)
```

In `_run_cubepi_path` near the existing `_on_event` registration, chain the auto-detach listener:

```python
auto_detach = _build_auto_detach_listener(agent)

def _on_event(evt: Any, _signal: Any = None) -> None:
    # ... existing body
    auto_detach(evt, _signal)
    # ... existing SSE conversion

agent.subscribe(_on_event)
```

(Track `saw_hitl_request` on a flag the terminal block can read — see Task 6.)

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_run_manager_auto_detach.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/unit/test_run_manager_auto_detach.py
git commit -m "feat(hitl): auto-detach worker on HitlRequestEvent"
```

---

## Task 6: `_classify_terminal_status` helper + dangling-pending cleanup

**Files:**
- Modify: `backend/cubebox/streams/hitl_resume.py` (add helper)
- Modify: `backend/cubebox/streams/run_manager.py` (terminal block uses helper)
- Test: `backend/tests/unit/test_run_manager_classify_terminal_status.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_classify_terminal_status.py
import pytest
from unittest.mock import MagicMock

from cubebox.streams.hitl_resume import classify_terminal_status, TerminalClassification


def _fake_pending(qid: str):
    p = MagicMock()
    p.question_id = qid
    return p


def test_no_pending_completes():
    r = classify_terminal_status(
        final_pending=None,
        answered_question_id=None,
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="completed", clear_pending=False)


def test_pending_without_hitl_event_is_stale():
    r = classify_terminal_status(
        final_pending=_fake_pending("q-leftover"),
        answered_question_id=None,
        saw_hitl_request_event=False,
    )
    assert r == TerminalClassification(status="completed", clear_pending=True)


def test_respond_dangling_pending_clears():
    r = classify_terminal_status(
        final_pending=_fake_pending("q-original"),
        answered_question_id="q-original",
        saw_hitl_request_event=False,
    )
    assert r == TerminalClassification(status="completed", clear_pending=True)


def test_respond_new_pending_paused():
    r = classify_terminal_status(
        final_pending=_fake_pending("q-new"),
        answered_question_id="q-original",
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="paused_hitl", clear_pending=False)


def test_prompt_new_pending_paused():
    r = classify_terminal_status(
        final_pending=_fake_pending("q-new"),
        answered_question_id=None,
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="paused_hitl", clear_pending=False)
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_classify_terminal_status.py -v
```
Expected: FAIL — symbol not defined.

- [ ] **Step 3: Implement**

Append to `backend/cubebox/streams/hitl_resume.py`:

```python
@dataclass(frozen=True)
class TerminalClassification:
    status: str            # "completed" | "paused_hitl"
    clear_pending: bool    # caller should cp.save_pending_request(cid, None)


def classify_terminal_status(
    *,
    final_pending: Any | None,        # HitlRequest or None
    answered_question_id: str | None, # None on prompt path
    saw_hitl_request_event: bool,
) -> TerminalClassification:
    """See spec §6 "Dangling pending cleanup"."""
    if final_pending is None:
        return TerminalClassification(status="completed", clear_pending=False)
    if not saw_hitl_request_event:
        # Pending in DB but this turn never emitted a HitlRequestEvent →
        # leftover from prior session. Clear and treat as completed.
        return TerminalClassification(status="completed", clear_pending=True)
    if answered_question_id is not None and final_pending.question_id == answered_question_id:
        # Respond path dangling: middleware short-circuited the resumed call.
        return TerminalClassification(status="completed", clear_pending=True)
    return TerminalClassification(status="paused_hitl", clear_pending=False)
```

In `run_manager.py`'s `_run_cubepi_path` terminal block (after `agent.prompt()` returns, inside the `finally` or `else`):

```python
from cubebox.streams.hitl_resume import classify_terminal_status

final_pending = await agent.load_pending_hitl_request()
classification = classify_terminal_status(
    final_pending=final_pending,
    answered_question_id=None,  # prompt path
    saw_hitl_request_event=auto_detach.detached,  # expose the flag
)
if classification.clear_pending:
    await cp.save_pending_request(conversation_id, None)
final_status = classification.status
```

(Modify `_build_auto_detach_listener` to expose `.detached` as an attribute so the terminal block can read it.)

Wire `final_status` into the existing run-finalization code (replace the spot that currently sets `"completed"` / `"errored"`).

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_run_manager_classify_terminal_status.py -v
uv run pytest tests/unit/test_run_manager_auto_detach.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/hitl_resume.py \
        backend/cubebox/streams/run_manager.py \
        backend/tests/unit/test_run_manager_classify_terminal_status.py
git commit -m "feat(hitl): classify terminal status with dangling-pending cleanup"
```

---

## Task 7: Extract `_build_agent_for_conversation` factory

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (extract method)
- Test: `backend/tests/unit/test_run_manager_build_agent.py` (NEW — smoke test the factory)

- [ ] **Step 1: Write the failing test (smoke)**

```python
# backend/tests/unit/test_run_manager_build_agent.py
"""Smoke test: the build factory produces the same shape inline as the
old _run_cubepi_path did. We don't snapshot middleware identities (that
would be brittle); we assert the returned tuple shape and that the
channel is a CheckpointedChannel."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_build_agent_returns_tuple_with_checkpointed_channel():
    # Build will need: ctx, conversation_id, cp, sandbox (None ok),
    # skill_catalog (None ok), catalog_session (None ok),
    # effective_system_prompt, extra_ref (empty dict).
    # Use the same fixtures the existing run_manager tests use.
    pytest.skip("Implement after factory exists; see Task 7 step 3")
```

(This test is a placeholder; the factory's interface settles in step 3. After step 3, replace with a real smoke test calling the factory with minimal fixtures.)

- [ ] **Step 2: Run to verify SKIP**

```
uv run pytest tests/unit/test_run_manager_build_agent.py -v
```
Expected: 1 skipped.

- [ ] **Step 3: Implement the factory**

In `backend/cubebox/streams/run_manager.py`, extract sections 1–8 (provider + middleware + tools + channel + agent build) of `_run_cubepi_path` into:

```python
async def _build_agent_for_conversation(
    self,
    *,
    ctx: RunContext,
    conversation_id: str,
    run_id: str,                       # passed to CheckpointedChannel for atomic run_id persistence
    cp: PostgresCheckpointer,
    sandbox: Any | None,
    skill_catalog: Any | None,
    catalog_session: Any | None,
    effective_system_prompt: str,
    extra_ref_holder: dict[str, Any],
    sse_queue: asyncio.Queue,
    publish_stream_event: Any,
) -> tuple[Agent, list[Any], HitlChannel | None]:
    """Build provider + middleware + tools + channel + agent for a
    conversation. Shared by prompt and respond paths.

    The channel is constructed as
    CheckpointedChannel(checkpointer=cp, thread_id=conversation_id,
                        run_id=run_id, default_timeout=None)
    so every pause writes pending + run_id in one atomic statement.

    Returns (agent, all_tools, sandbox_hitl_channel).
    """
    # ... move sections 1–8 here verbatim, parameterizing on the inputs above
```

Then `_run_cubepi_path` becomes (skeleton):

```python
async with init_checkpointer() as cp:
    agent, all_tools, sandbox_hitl_channel = await self._build_agent_for_conversation(
        ctx=ctx,
        conversation_id=conversation_id,
        run_id=run_id,                      # atomic run_id persistence
        cp=cp,
        sandbox=sandbox,
        skill_catalog=skill_catalog,
        catalog_session=catalog_session,
        effective_system_prompt=effective_system_prompt,
        extra_ref_holder=extra_ref_holder,
        sse_queue=sse_queue,
        publish_stream_event=publish_stream_event,
    )
    auto_detach = _build_auto_detach_listener(agent)
    agent.subscribe(_on_event)  # wraps auto_detach + sse conversion
    self._agents[run_id] = agent
    if sandbox_hitl_channel is not None:
        self._hitl_channels[run_id] = sandbox_hitl_channel
    # ... build user message, call agent.prompt(), terminal classification
```

Replace the smoke test from step 1 with an actual minimal-fixtures call. Use the existing test patterns in `tests/unit/` for RunManager construction — `test_run_manager_cubepi_dict_to_event.py` has a builder.

- [ ] **Step 4: Run all RunManager tests**

```
uv run pytest tests/unit/test_run_manager_build_agent.py tests/unit/test_run_manager_auto_detach.py tests/unit/test_run_manager_classify_terminal_status.py tests/unit/test_run_manager_cubepi_dict_to_event.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/tests/unit/test_run_manager_build_agent.py
git commit -m "refactor(runs): extract _build_agent_for_conversation factory"
```

---

## Task 8: `_run_cubepi_respond_path` + `_execute_respond_run`

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py`
- Test: covered by Task 9's integration test

- [ ] **Step 1: Implement `_run_cubepi_respond_path`**

In `backend/cubebox/streams/run_manager.py`, add a sibling to `_run_cubepi_path`:

```python
async def _run_cubepi_respond_path(
    self,
    *,
    ctx: RunContext,
    run_id: str,
    conversation_id: str,
    question_id: str,
    answer: Any,
    claim_token: str,
    effective_system_prompt: str,
    publish_stream_event: Any,
    flush_citation_buffer: Any,
    citation_buffers: dict[str | None, str],
    sandbox: Any | None = None,
    skill_catalog: Any | None = None,
    catalog_session: Any | None = None,
) -> None:
    """Resume a paused HITL conversation. Reuses the run_id of the paused
    turn; events stream into the same Redis key."""
    from cubebox.streams.hitl_resume import classify_terminal_status
    # ... boilerplate similar to _run_cubepi_path: extra_ref_holder,
    # SSE queue, drainer, citation seed, etc.

    async with init_checkpointer() as cp:
        agent, all_tools, sandbox_hitl_channel = await self._build_agent_for_conversation(
            ctx=ctx,
            conversation_id=conversation_id,
            run_id=run_id,                      # atomic run_id persistence
            cp=cp,
            sandbox=sandbox,
            skill_catalog=skill_catalog,
            catalog_session=catalog_session,
            effective_system_prompt=effective_system_prompt,
            extra_ref_holder=extra_ref_holder,
            sse_queue=sse_queue,
            publish_stream_event=publish_stream_event,
        )
        auto_detach = _build_auto_detach_listener(agent)
        # ... subscribe _on_event chain
        self._agents[run_id] = agent
        if sandbox_hitl_channel is not None:
            self._hitl_channels[run_id] = sandbox_hitl_channel

        # Trace the respond invocation the same way _run_cubepi_path
        # traces prompt (see run_manager.py:1797-1819). Same _trace_meta
        # keys + run_id; the recorder stamps `cubepi.metadata.run_id` so
        # paused/resumed turns group together in trace storage by run_id.
        from cubepi.tracing import trace, tracing_context
        tracer = getattr(self._app.state, "tracer", None)
        _trace_meta = {
            k: str(v) for k, v in (
                ("run_id", run_id),               # ← key for paused/resumed grouping
                ("conversation_id", conversation_id),
                ("user_id", ctx.user_id),
                ("org_id", ctx.org_id),
                ("workspace_id", ctx.workspace_id),
                ("turn_kind", "respond"),         # ← distinguishes from "prompt"
            ) if v is not None
        }
        try:
            with tracing_context(metadata=_trace_meta):
                async with trace(tracer, agent):
                    await agent.respond(question_id=question_id, answer=answer)
        finally:
            final_pending = await agent.load_pending_hitl_request()
            classification = classify_terminal_status(
                final_pending=final_pending,
                answered_question_id=question_id,
                saw_hitl_request_event=auto_detach.detached,
            )
            if classification.clear_pending:
                await cp.save_pending_request(conversation_id, None)
                # Emit synthetic resolved event — see Task 12.
                await _emit_synthetic_resolved(
                    publish_stream_event,
                    final_pending,
                    question_id,
                )
            # Write terminal status only if our claim token still matches.
            await finalize_run_meta_if_claim_matches(
                self._redis, prefix=self._key_prefix,
                run_id=run_id, claim_token=claim_token,
                status=classification.status,
            )
            self._agents.pop(run_id, None)
            self._hitl_channels.pop(run_id, None)
            await sse_queue.put(None)
            await drainer
```

`_finalize_run_meta_if_claim_matches` is a small helper appended to `hitl_resume.py`:

```python
# KEYS[1] = meta_key
# ARGV[1] = expected_claim_token, ARGV[2] = new_status
# Returns 1 if status was set, 0 if token mismatch (caller's claim was
# superseded by some other flow — do not clobber).
_FINALIZE_IF_CLAIM_MATCHES_LUA = """
if redis.call('HGET', KEYS[1], 'claim_token') ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'status', ARGV[2])
return 1
"""


async def finalize_run_meta_if_claim_matches(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    claim_token: str,
    status: str,
) -> bool:
    result = await redis.eval(
        _FINALIZE_IF_CLAIM_MATCHES_LUA, 1,
        _run_meta_key(prefix, run_id),
        claim_token, status,
    )
    return int(result) == 1
```

Import it in `run_manager.py` and call as `await finalize_run_meta_if_claim_matches(self._redis, prefix=self._key_prefix, run_id=run_id, claim_token=claim_token, status=classification.status)`.

`_emit_synthetic_resolved` is implemented in Task 12; stub it here as `async def _emit_synthetic_resolved(*a, **k): pass` and replace in Task 12.

- [ ] **Step 2: Implement `_execute_respond_run` task wrapper**

Mirrors `_execute_run` — wraps `_run_cubepi_respond_path` in error handling, schedules a background task. Add to `RunManager`:

```python
async def _execute_respond_run(
    self,
    *,
    run_id: str,
    conversation_id: str,
    question_id: str,
    answer: Any,
    claim_token: str,
    ctx: RunContext,
) -> None:
    try:
        await self._run_cubepi_respond_path(
            ctx=ctx,
            run_id=run_id,
            conversation_id=conversation_id,
            question_id=question_id,
            answer=answer,
            claim_token=claim_token,
            effective_system_prompt=...,  # re-derive same as _execute_run
            publish_stream_event=...,
            flush_citation_buffer=...,
            citation_buffers={},
        )
    except Exception:
        logger.exception("respond run failed for {}", run_id)
        # Don't clear DB pending — leaving it allows the user to retry.
```

- [ ] **Step 3: Compile-check**

```
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond/backend
uv run mypy cubebox/streams/run_manager.py cubebox/streams/hitl_resume.py
```
Expected: zero errors. Fix any.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/cubebox/streams/hitl_resume.py
git commit -m "feat(hitl): add respond path that resumes paused conversations"
```

---

## Task 9: `resume_run_with_answer` method + answer routes

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (add method)
- Modify: `backend/cubebox/api/routes/v1/conversations.py` (`submit_ask_user_answer`, sandbox confirm answer route)
- Test: `backend/tests/unit/test_run_manager_resume_run_with_answer.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_resume_run_with_answer.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio


async def test_resume_returns_404_when_no_pending(monkeypatch):
    from cubebox.streams.run_manager import RunManager
    rm = ... # minimal RunManager
    cp_mock = AsyncMock()
    cp_mock.load_pending_request = AsyncMock(return_value=None)
    monkeypatch.setattr("cubebox.streams.run_manager.init_checkpointer", lambda: _fake_cm(cp_mock))
    with pytest.raises(LookupError):  # mapped to 404 at the route
        await rm.resume_run_with_answer(
            conversation_id="c1", run_id="r1",
            question_id="q1", answer={}, ctx=...
        )


async def test_resume_returns_409_on_qid_mismatch(monkeypatch):
    cp_mock = AsyncMock()
    pending = MagicMock(); pending.question_id = "q-actual"
    pending.created_at = 1700000000.0  # for started_at derivation
    cp_mock.load_pending_request = AsyncMock(return_value=pending)
    # ... patch + call rm.resume_run_with_answer with question_id="q-wrong"
    # ResumeStaleAnswer is the explicit exception type; the route maps it to 409.
    from cubebox.streams.run_manager import ResumeStaleAnswer
    with pytest.raises(ResumeStaleAnswer):
        await rm.resume_run_with_answer(
            conversation_id="c1", run_id="r1",
            question_id="q-wrong", answer={}, ctx=...
        )


async def test_resume_returns_409_on_already_running(monkeypatch):
    # claim_resume returns ALREADY_RUNNING → method raises a specific
    # ConflictError mapped to 409 resume_in_flight at the route.
    ...


async def test_resume_happy_path_spawns_respond_task(monkeypatch):
    # claim_resume returns OK; pending matches qid; method
    # asyncio.create_task(_execute_respond_run(...)) and returns run_id.
    ...
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_resume_run_with_answer.py -v
```

- [ ] **Step 3: Implement**

In `backend/cubebox/streams/run_manager.py`:

```python
class ResumeStaleAnswer(Exception): ...
class ResumeNoPending(LookupError): ...
class ResumeInFlight(Exception): ...
class ResumeConflict(Exception): ...


async def resume_run_with_answer(
    self,
    *,
    conversation_id: str,
    run_id: str,
    question_id: str,
    answer: Any,
    ctx: RunContext,
) -> str:
    """See spec §5."""
    from cubebox.streams.hitl_resume import claim_resume, ClaimResumeOutcome

    # 1. Authoritative: DB pending. (load_pending_request shape unchanged
    #    per cubepi prerequisite — only the new run_id column gets its own
    #    method.)
    async with init_checkpointer() as cp:
        pending = await cp.load_pending_request(conversation_id)
    if pending is None:
        raise ResumeNoPending(f"no pending for {conversation_id}")
    if pending.question_id != question_id:
        raise ResumeStaleAnswer(
            f"answer for {question_id}; pending is {pending.question_id}"
        )
    started_at_iso = datetime.fromtimestamp(pending.created_at, UTC).isoformat()

    # 2. Single-flight claim — pass started_at so the long-pause rebuild
    #    branch in claim_resume's Lua can repopulate the meta hash.
    claim = await claim_resume(
        self._redis, prefix=self._key_prefix,
        conversation_id=conversation_id,
        expected_run_id=run_id,
        started_at=started_at_iso,
        ttl_seconds=self._run_event_ttl_seconds,
    )
    if claim.outcome == ClaimResumeOutcome.ALREADY_RUNNING:
        raise ResumeInFlight("another resume/cancel is in flight")
    if claim.outcome == ClaimResumeOutcome.CONFLICT:
        raise ResumeConflict("conversation has moved on")

    # 3. Spawn the respond task.
    task = asyncio.create_task(
        self._execute_respond_run(
            run_id=run_id,
            conversation_id=conversation_id,
            question_id=question_id,
            answer=answer,
            claim_token=claim.claim_token,
            ctx=ctx,
        ),
        name=f"respond:{run_id}",
    )
    self._tasks_empty.clear()
    self._tasks[run_id] = task
    task.add_done_callback(lambda _: self._on_task_done(run_id))
    return run_id
```

- [ ] **Step 4: Wire the routes**

In `backend/cubebox/api/routes/v1/conversations.py`:

**Remove the existing `status != "running"` precheck on lines 1165 + 1205** (the two answer routes) — paused_hitl is exactly the state that should accept answers. Same precheck on the cancel route (line 1051) gets handled in Task 10; steer/cancel_steer (1091, 1127) get handled there too.

For `submit_ask_user_answer` (around line 1179):

```python
# Get run_id from Redis active-run record (hot path), or from the
# checkpointer's load_pending_run_id (cold path — Redis TTL expired
# during a long pause).
active_run = await get_active_run(rds.client, prefix=rds.key_prefix, conversation_id=conversation_id)
if active_run is not None:
    run_id = active_run.run_id
else:
    async with init_checkpointer() as _cp:
        persisted_run_id = await _cp.load_pending_run_id(conversation_id)
    if persisted_run_id is None:
        # No DB pending either OR pre-prereq legacy row with no run_id.
        # Distinguish: check load_pending_request to differentiate 404 vs 500.
        async with init_checkpointer() as _cp:
            if await _cp.load_pending_request(conversation_id) is None:
                raise HTTPException(status_code=404, detail={"code": "no_pending"})
        raise HTTPException(
            status_code=500,
            detail={"code": "missing_run_id", "message": "pending has no persisted run_id (legacy row)"},
        )
    run_id = persisted_run_id

try:
    new_run_id = await run_manager.resume_run_with_answer(
        conversation_id=conversation_id,
        run_id=run_id,
        question_id=body.question_id,
        answer=body.answers,
        ctx=ctx,
    )
except ResumeNoPending:
    raise HTTPException(status_code=404, detail={"code": "no_pending"})
except ResumeStaleAnswer:
    raise HTTPException(status_code=409, detail={"code": "stale_answer"})
except ResumeInFlight:
    raise HTTPException(status_code=409, detail={"code": "resume_in_flight"})
except ResumeConflict:
    raise HTTPException(status_code=409, detail={"code": "conversation_moved"})
return {"run_id": new_run_id}
```

For the sandbox confirm answer route (`submit_sandbox_confirm`, around line 1139):

```python
# SandboxConfirmAnswer.decision is already Literal["approve", "deny"]
# (api/routes/v1/conversations.py:369) — matches cubepi's
# ApproveAnswer.decision Literal["approve","deny","edit"] vocabulary.
# Pass through directly; no field-mapping table needed.
from cubepi.hitl.types import ApproveAnswer

answer = ApproveAnswer(decision=body.decision, reason=body.reason)
# Resolve run_id same as above (active_run or fallback to persisted run_id).
# ... then call run_manager.resume_run_with_answer with the same try/except chain.
```

- [ ] **Step 5: Run targeted tests**

```
uv run pytest tests/unit/test_run_manager_resume_run_with_answer.py -v
uv run pytest tests/unit/test_sandbox_confirm_gate.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/streams/run_manager.py \
        backend/cubebox/api/routes/v1/conversations.py \
        backend/tests/unit/test_run_manager_resume_run_with_answer.py
git commit -m "feat(hitl): resume_run_with_answer + 404/409 route mapping"
```

---

## Task 10: `cancel_paused_run` for paused-state cancellation

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py`
- Modify: `backend/cubebox/api/routes/v1/conversations.py` (cancel route)
- Test: `backend/tests/unit/test_run_manager_cancel_paused.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_cancel_paused.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_cancel_paused_runs_abort_pending(monkeypatch):
    """When called on a paused conversation, cancel must build a transient
    agent and call abort_pending — not just clear DB rows."""
    # claim_resume → OK; build agent; agent.abort_pending called; DB pending
    # cleared as a side effect; final status = cancelled.
    ...


async def test_cancel_paused_returns_conflict_when_running(monkeypatch):
    """claim_resume → ALREADY_RUNNING → cancel_paused_run raises ResumeInFlight."""
    ...
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_cancel_paused.py -v
```

- [ ] **Step 3: Implement**

```python
async def cancel_paused_run(
    self,
    *,
    conversation_id: str,
    run_id: str,
    reason: str = "cancelled by user",
    ctx: RunContext,
) -> str:
    """See spec §4 — cancel-on-paused branch.

    The transient agent built here MUST wire the same SSE publisher
    and _on_event listener chain as a normal run. Otherwise
    agent.abort_pending's AgentAbortedEvent goes to ZERO listeners —
    the frontend never sees the cancel, pending cards stay rendered,
    composer stays locked. Reuse the existing publish_stream_event
    builder + convert_agent_event_to_sse chain.
    """
    from cubebox.streams.hitl_resume import (
        claim_resume, finalize_run_meta_if_claim_matches, ClaimResumeOutcome,
    )
    from cubebox.agents.stream import convert_agent_event_to_sse

    # Need started_at for claim_resume's rebuild branch (long-pause case).
    async with init_checkpointer() as cp:
        pending = await cp.load_pending_request(conversation_id)
    if pending is None:
        raise ResumeNoPending(f"no pending for {conversation_id}")
    started_at_iso = datetime.fromtimestamp(pending.created_at, UTC).isoformat()

    claim = await claim_resume(
        self._redis, prefix=self._key_prefix,
        conversation_id=conversation_id, expected_run_id=run_id,
        started_at=started_at_iso,
        ttl_seconds=self._run_event_ttl_seconds,
    )
    if claim.outcome == ClaimResumeOutcome.ALREADY_RUNNING:
        raise ResumeInFlight("cancel raced another resume/cancel in flight")
    if claim.outcome == ClaimResumeOutcome.CONFLICT:
        raise ResumeConflict("conversation has moved on")

    # Set up a real SSE pipeline so AgentAbortedEvent reaches the frontend.
    sse_queue: asyncio.Queue = asyncio.Queue()
    publish_stream_event = self._make_publish_stream_event(
        run_id=run_id, conversation_id=conversation_id,
    )  # same helper start_run / _execute_run use; extract if not already shared
    drainer = asyncio.create_task(
        _drain_cubepi_sse_queue(sse_queue, publish_stream_event)
    )

    try:
        async with init_checkpointer() as cp:
            agent, _tools, _ch = await self._build_agent_for_conversation(
                ctx=ctx, conversation_id=conversation_id,
                run_id=run_id,                      # atomic — same channel contract
                cp=cp,
                sandbox=None, skill_catalog=None, catalog_session=None,
                effective_system_prompt="",  # unused for abort
                extra_ref_holder={},
                sse_queue=sse_queue,
                publish_stream_event=publish_stream_event,
            )

            def _on_event(evt, _signal=None):
                for d in convert_agent_event_to_sse(evt):
                    sse_queue.put_nowait(d)
            agent.subscribe(_on_event)

            # Trace the abort, same shape as prompt/respond, distinguished by turn_kind.
            from cubepi.tracing import trace, tracing_context
            tracer = getattr(self._app.state, "tracer", None)
            _trace_meta = {
                k: str(v) for k, v in (
                    ("run_id", run_id),
                    ("conversation_id", conversation_id),
                    ("user_id", ctx.user_id),
                    ("org_id", ctx.org_id),
                    ("workspace_id", ctx.workspace_id),
                    ("turn_kind", "abort"),
                ) if v is not None
            }
            with tracing_context(metadata=_trace_meta):
                async with trace(tracer, agent):
                    await agent.abort_pending(reason)
    finally:
        await sse_queue.put(None)
        await drainer

    await finalize_run_meta_if_claim_matches(
        self._redis, prefix=self._key_prefix,
        run_id=run_id, claim_token=claim.claim_token,
        status="cancelled",
    )
    return run_id
```

**Note:** `_build_agent_for_conversation` (Task 7) must handle
`sandbox=None`, `skill_catalog=None`, `catalog_session=None`,
`effective_system_prompt=""` gracefully — each middleware section in
the factory needs to be guarded so the "minimal abort agent" shape
works. Verify during Task 7 implementation; section 6 already runs
`if sandbox is not None`, follow that pattern for the others.

**Route updates in `conversations.py`:**

(a) Cancel route (line 1051):

```python
if active_run is None:
    return {"status": "no_active_run", "run_id": None}
if active_run.status == "paused_hitl":
    try:
        await run_manager.cancel_paused_run(
            conversation_id=conversation_id,
            run_id=active_run.run_id,
            reason="cancelled by user",
            ctx=ctx,
        )
        return {"status": "cancelled", "run_id": active_run.run_id}
    except ResumeInFlight:
        raise HTTPException(status_code=409, detail={"code": "resume_in_flight"})
    except ResumeConflict:
        raise HTTPException(status_code=409, detail={"code": "conversation_moved"})
if active_run.status != "running":
    return {"status": "no_active_run", "run_id": None}
# ... existing cancel_run path for status="running"
```

(b) Steer + cancel_steer routes (lines 1091, 1127): paused conversations must surface a clear error rather than the misleading `no_active_run`:

```python
if active_run is not None and active_run.status == "paused_hitl":
    raise HTTPException(
        status_code=409,
        detail={"code": "paused_hitl", "message": "answer or cancel the pending question first"},
    )
if active_run is None or active_run.status != "running":
    return {"status": "no_active_run", "run_id": None}
```

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_run_manager_cancel_paused.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py \
        backend/cubebox/api/routes/v1/conversations.py \
        backend/tests/unit/test_run_manager_cancel_paused.py
git commit -m "feat(hitl): cancel_paused_run via claim_resume + abort_pending"
```

---

## Task 11: Remove dispatched answer plumbing

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (delete `dispatch_ask_user_answer`, `dispatch_hitl_answer`, `_deliver_ask_user_answer`, `_deliver_hitl_answer`; delete the `ask_user_answer` + `hitl_answer` arms of `_handle_control`)
- Modify: `backend/cubebox/api/routes/v1/conversations.py` (remove any remaining call to those dispatch methods — should be replaced by Task 9)
- **Delete: `backend/tests/unit/test_run_manager_ask_user_answer.py`** (123 lines; tests the deleted method directly)
- **Delete: `backend/tests/unit/test_run_manager_hitl_answer.py`** (91 lines; tests the deleted method directly)

(The coverage these gave for "in-process answer delivery + Redis-pubsub fallback" is replaced by Task 9's `test_run_manager_resume_run_with_answer.py`, which exercises the new single entry point.)

- [ ] **Step 1: Verify no other callers exist**

```bash
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond
grep -rn "dispatch_ask_user_answer\|dispatch_hitl_answer\|_deliver_ask_user_answer\|_deliver_hitl_answer\|publish_control.*ask_user_answer\|publish_control.*hitl_answer" backend/cubebox backend/tests
```
Expected after Tasks 9–10: matches inside `run_manager.py` itself (the definitions) AND the two test files listed above; no other external callers. The test files are deleted in step 2.

- [ ] **Step 2: Delete the methods + control arms + obsolete tests**

In `backend/cubebox/streams/run_manager.py`, delete the four methods listed above and the corresponding `elif type_ == "ask_user_answer"` / `elif type_ == "hitl_answer"` branches in `_handle_control`. Remove now-unused imports.

```bash
git rm backend/tests/unit/test_run_manager_ask_user_answer.py
git rm backend/tests/unit/test_run_manager_hitl_answer.py
```

- [ ] **Step 3: Run the full backend test suite**

```
cd backend
uv run pytest tests/unit -x -q
```
Expected: all PASS (no test relies on the removed methods).

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/streams/run_manager.py backend/cubebox/api/routes/v1/conversations.py
git commit -m "refactor(hitl): drop Redis-pubsub answer dispatch (replaced by resume_run_with_answer)"
```

---

## Task 12: Synthetic resolved event on dangling cleanup

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py` (`_emit_synthetic_resolved` real impl)
- Modify: `backend/cubebox/agents/schemas.py` (doc-only: extend `decision` field docstring to mention `"policy_overridden"`)
- Test: `backend/tests/unit/test_run_manager_dangling_cleanup_event.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_manager_dangling_cleanup_event.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubebox.agents.schemas import SandboxConfirmResolvedEvent, AskUserResolvedEvent

pytestmark = pytest.mark.asyncio


async def test_dangling_sandbox_cleanup_emits_resolved_event():
    """When respond's terminal block clears a dangling sandbox confirm
    pending, a typed SandboxConfirmResolvedEvent with decision='policy_overridden'
    is published via the same publish_stream_event(event, agent_key) the
    rest of the run uses — NOT a raw dict (publish_stream_event accesses
    event.type and event.data)."""
    publish = AsyncMock()
    from cubebox.streams.run_manager import _emit_synthetic_resolved
    pending = MagicMock()
    pending.payload.kind = "approve"
    pending.payload.tool_call_id = "tc1"
    await _emit_synthetic_resolved(publish, pending, "q1")
    publish.assert_awaited_once()
    args, kwargs = publish.await_args
    event = args[0]
    assert isinstance(event, SandboxConfirmResolvedEvent)
    assert event.data["question_id"] == "q1"
    assert event.data["tool_call_id"] == "tc1"
    assert event.data["decision"] == "policy_overridden"


async def test_dangling_ask_cleanup_emits_resolved_event():
    publish = AsyncMock()
    from cubebox.streams.run_manager import _emit_synthetic_resolved
    pending = MagicMock()
    pending.payload.kind = "ask"
    await _emit_synthetic_resolved(publish, pending, "q1")
    publish.assert_awaited_once()
    event = publish.await_args.args[0]
    assert isinstance(event, AskUserResolvedEvent)
    assert event.data["question_id"] == "q1"
    # AskUserResolvedEvent.data schema is {question_id, answers, cancelled, timed_out}
    # (schemas.py:219-221). We use cancelled=True + a reason in details to convey
    # 'policy_overridden' WITHOUT adding a new field the live SSE path doesn't have.
    assert event.data["cancelled"] is True
    assert event.data.get("reason") == "policy_overridden"


async def test_dangling_cleanup_raises_on_unknown_kind():
    """Future cubepi kind (e.g. 'confirm') must surface loudly, not silently
    drop the synthetic event — otherwise the frontend card sticks."""
    publish = AsyncMock()
    from cubebox.streams.run_manager import _emit_synthetic_resolved
    pending = MagicMock()
    pending.payload.kind = "confirm"  # cubepi ConfirmRequest, not used by cubebox today
    with pytest.raises(ValueError, match="unhandled HITL kind"):
        await _emit_synthetic_resolved(publish, pending, "q1")
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_run_manager_dangling_cleanup_event.py -v
```

- [ ] **Step 3: Implement**

In `backend/cubebox/streams/run_manager.py`:

```python
async def _emit_synthetic_resolved(
    publish_stream_event: Any,
    pending: Any,
    answered_question_id: str,
) -> None:
    """Emit a typed *_resolved event for a pending that was cleared by
    the dangling-cleanup branch (org policy changed between pause and
    respond, so middleware short-circuited the resumed tool call).

    Uses the SAME typed events + publish_stream_event(event, agent_key)
    signature the live HITL resolve path uses — so the frontend sees an
    identical event shape and the same applyStreamEvent branch fires.

    See spec §6 "Dangling pending cleanup".
    """
    from cubebox.agents.schemas import (
        SandboxConfirmResolvedEvent, AskUserResolvedEvent,
    )

    kind = pending.payload.kind  # "approve" | "ask" | "confirm"
    if kind == "approve":
        event = SandboxConfirmResolvedEvent(
            data={
                "question_id": answered_question_id,
                "tool_call_id": pending.payload.tool_call_id,
                "decision": "policy_overridden",
                "cancelled": False,
                "timed_out": False,
                "reason": "org sandbox policy changed during pause",
            },
        )
    elif kind == "ask":
        # AskUserResolvedEvent.data is {question_id, answers, cancelled,
        # timed_out} — no 'outcome' field. Encode policy-override as
        # cancelled=True + reason='policy_overridden' so the existing
        # frontend applyStreamEvent ask_user_resolved branch fires and
        # the card is removed.
        event = AskUserResolvedEvent(
            data={
                "question_id": answered_question_id,
                "answers": None,
                "cancelled": True,
                "timed_out": False,
                "reason": "policy_overridden",
            },
        )
    else:
        # ConfirmRequest (kind='confirm') is unused by cubebox today. If a
        # future caller introduces it, fail loud rather than silently leave
        # the frontend with a stuck card.
        raise ValueError(f"unhandled HITL kind in dangling cleanup: {kind!r}")

    await publish_stream_event(event, None)  # second arg = agent_key
```

Update the `SandboxConfirmResolvedEvent` docstring in
`backend/cubebox/agents/schemas.py` to enumerate `"policy_overridden"`
as a possible `decision` value. The `AskUserResolvedEvent` schema is
unchanged — we ride on `cancelled=True` + `reason` rather than adding
a new `outcome` field that would diverge from the live SSE path.

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_run_manager_dangling_cleanup_event.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/streams/run_manager.py \
        backend/cubebox/agents/schemas.py \
        backend/tests/unit/test_run_manager_dangling_cleanup_event.py
git commit -m "feat(hitl): emit synthetic resolved event when dangling pending is cleaned"
```

---

## Task 13: `pending_hitl` in conversation bootstrap

**Files:**
- Modify: `backend/cubebox/api/routes/v1/conversations.py` (bootstrap endpoint)
- Modify: `backend/cubebox/api/schemas/conversations.py` (or where bootstrap response is typed) — add `PendingHitl` union
- Test: `backend/tests/unit/test_conversations_bootstrap_pending_hitl.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_conversations_bootstrap_pending_hitl.py
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio


async def test_bootstrap_includes_pending_hitl_when_db_has_pending(monkeypatch):
    """Cold-start fallback: bootstrap response carries enough to render the
    AskUserCard/SandboxConfirmCard without SSE replay."""
    # Patch checkpointer.load_pending_request to return a pending with kind="ask"
    # Hit the bootstrap endpoint via TestClient. Assert response JSON has
    # pending_hitl.kind == "ask_user" and a populated questions list.
    ...


async def test_bootstrap_pending_hitl_null_when_no_pending(monkeypatch):
    """No pending in DB → pending_hitl is null."""
    ...
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_conversations_bootstrap_pending_hitl.py -v
```

- [ ] **Step 3: Implement**

In `backend/cubebox/api/schemas/conversations.py` (or correct file):

```python
from typing import Literal
from pydantic import BaseModel


class AskUserQuestion(BaseModel):
    key: str
    prompt: str
    options: list[dict] | None = None  # {label, value, description?}
    multi_select: bool
    required: bool


class PendingHitlAskUser(BaseModel):
    run_id: str
    question_id: str
    kind: Literal["ask_user"]
    requested_at: str
    questions: list[AskUserQuestion]


class PendingHitlSandboxConfirm(BaseModel):
    run_id: str
    question_id: str
    kind: Literal["sandbox_confirm"]
    requested_at: str
    tool_call_id: str
    command: str
    matched_pattern: str


PendingHitl = PendingHitlAskUser | PendingHitlSandboxConfirm
```

In the bootstrap endpoint, after assembling the existing response:

```python
from cubebox.streams.run_events import get_active_run

async with init_checkpointer() as cp:
    pending_req = await cp.load_pending_request(conversation_id)
    persisted_run_id = await cp.load_pending_run_id(conversation_id)

pending_hitl: dict[str, Any] | None = None
if pending_req is not None:
    # Run_id resolution order: Redis active-run first (cheapest), DB-persisted fallback.
    active = await get_active_run(rds.client, prefix=rds.key_prefix, conversation_id=conversation_id)
    run_id = active.run_id if active is not None else persisted_run_id
    if run_id is None:
        # Legacy row (pre-cubepi-PR) — log + degrade to null so the user
        # can at least see other conversation state.
        logger.warning(
            "pending_request for {} has no recoverable run_id; pending_hitl set to null",
            conversation_id,
        )
    else:
        pending_hitl = serialize_pending_hitl(pending_req, run_id=run_id)

return {..., "pending_hitl": pending_hitl}
```

Where `_serialize_pending_hitl` lives in `cubebox/streams/hitl_resume.py`:

```python
def _as_dict(obj: Any) -> dict[str, Any]:
    """Pydantic .model_dump() if available, else assume already a dict
    (JSONB round-trip may produce either, depending on cubepi version)."""
    return obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)


def serialize_pending_hitl(pending: Any, *, run_id: str) -> dict[str, Any]:
    """Convert a cubepi HitlRequest → frontend PendingHitl payload (spec §7).

    Defensive against:
    - JSONB round-trip leaving inner objects as dicts (not Pydantic models)
    - ApproveRequest.details being None (cubepi default)
    """
    from cubebox.utils.time import utc_isoformat  # project convention
    from datetime import UTC, datetime

    requested_at = utc_isoformat(datetime.fromtimestamp(pending.created_at, UTC))
    kind = pending.payload.kind
    if kind == "approve":  # sandbox confirm
        args = pending.payload.args or {}
        details = pending.payload.details or {}
        return {
            "run_id": run_id,
            "question_id": pending.question_id,
            "kind": "sandbox_confirm",
            "requested_at": requested_at,
            "tool_call_id": pending.payload.tool_call_id,
            "command": args.get("command", ""),
            "matched_pattern": details.get("matched_pattern", ""),
        }
    if kind == "ask":
        questions_out = []
        for q in pending.payload.questions:
            q_d = _as_dict(q)
            opts = q_d.get("options")
            questions_out.append({
                "key": q_d["key"],
                "prompt": q_d["prompt"],
                "options": [_as_dict(o) for o in opts] if opts else None,
                "multi_select": q_d.get("multi_select", False),
                "required": q_d.get("required", True),
            })
        return {
            "run_id": run_id,
            "question_id": pending.question_id,
            "kind": "ask_user",
            "requested_at": requested_at,
            "questions": questions_out,
        }
    # confirm kind: unused by cubebox today. Raise so a future caller doesn't
    # silently get a half-built response.
    raise ValueError(f"unsupported pending HITL kind: {kind}")
```

**Run_id recovery contract** (resolves R3's "details['run_id']" issue —
that approach doesn't work because cubepi's `AskRequest` has no
`details` field; see "Upstream prerequisite" at the top of this plan):

Read order:

1. Redis `get_active_run(conversation_id)`. If meta is intact (paused_hitl
   row still alive), return `meta.run_id`. Cheapest; happy path.
2. Else (long-pause TTL recovery): call `cp.load_pending_run_id(conversation_id)`
   — the new method from the cubepi prerequisite. Authoritative.

```python
active = await get_active_run(rds.client, prefix=rds.key_prefix, conversation_id=conversation_id)
if active is not None:
    run_id = active.run_id
else:
    async with init_checkpointer() as cp:
        persisted_run_id = await cp.load_pending_run_id(conversation_id)
    if persisted_run_id is None:
        # Either no pending OR legacy row (pre-cubepi-PR) — degrade to None.
        logger.warning(
            "pending_request for {} has no persisted run_id; cannot reconstruct pending_hitl",
            conversation_id,
        )
        return None
    run_id = persisted_run_id
```

**Atomic run_id persistence (no terminal-block writes needed)**

Per the cubepi prerequisite, run_id flows through `CheckpointedChannel`
to `save_pending_request` in one atomic SQL statement. Both the prompt
path (Task 6) and the respond path (Task 8) build the channel via
`_build_agent_for_conversation(..., run_id=run_id, ...)`; when the
agent enters HITL, `_on_pending_set` writes pending + run_id together.
No separate cubebox call is required after `agent.load_pending_hitl_request()`
to "persist the run_id" — it was already persisted atomically when the
pending appeared.

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_conversations_bootstrap_pending_hitl.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/routes/v1/conversations.py \
        backend/cubebox/api/schemas/conversations.py \
        backend/cubebox/streams/hitl_resume.py \
        backend/tests/unit/test_conversations_bootstrap_pending_hitl.py
git commit -m "feat(api): pending_hitl in conversation bootstrap"
```

---

## Task 14: Frontend — bootstrap pending_hitl + composer lock + 409 handling

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts` (extend bootstrap type)
- Modify: `frontend/packages/core/src/stores/messageStore.ts` (loadMessages reads pending_hitl; submit handlers accept 409)
- Modify: `frontend/packages/web/components/chat/Composer.tsx` (or wherever composer lives — verify the file with grep)
- Test: `frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts` (NEW)

- [ ] **Step 1: Locate composer**

```bash
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond/frontend
grep -rln "send.*message\|Composer\|composer" packages/web/components/chat | head -5
```
Use the matching file for step 3.

- [ ] **Step 2: Confirm store shape first**

The live store has **`pendingAsk: PendingAsk | null` (singular)** and
**`pendingConfirmMap: Record<string, PendingConfirm>`** (a map keyed by
tool_call_id). Plan steps below match this — `pendingAsk` is NOT a map.

Existing field names on `PendingAsk` (from messageStore.ts):
`question_id`, `questions`, `requestedAt`, `timeout_seconds`,
`tool_call_id` (optional), plus whatever else the interface declares.
Verify by reading messageStore.ts:62-77 before writing the test.

- [ ] **Step 3: Write failing test**

```typescript
// frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts
import { describe, it, expect, vi } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

describe('bootstrap pending_hitl', () => {
  it('seeds pendingAsk (singular) when bootstrap includes ask_user pending_hitl', async () => {
    const fakeClient = {
      get: vi.fn().mockResolvedValue({
        messages: [], active_run: null,
        pending_hitl: {
          run_id: 'r1', question_id: 'q1', kind: 'ask_user',
          requested_at: '2026-06-02T00:00:00Z',
          questions: [
            { key: 'a', prompt: 'pick', options: [{label: 'X', value: 'x'}], multi_select: false, required: true },
          ],
        },
      }),
    }
    await useMessageStore.getState().loadMessages(fakeClient as any, 'conv1')
    const ask = useMessageStore.getState().pendingAsk
    expect(ask).toBeTruthy()
    expect(ask!.question_id).toBe('q1')
    expect(ask!.questions[0].key).toBe('a')
  })

  it('seeds pendingConfirmMap (keyed by tool_call_id) for sandbox_confirm', async () => {
    const fakeClient = {
      get: vi.fn().mockResolvedValue({
        messages: [], active_run: null,
        pending_hitl: {
          run_id: 'r1', question_id: 'q1', kind: 'sandbox_confirm',
          requested_at: '2026-06-02T00:00:00Z',
          tool_call_id: 'tc1', command: 'rm -rf /tmp/x', matched_pattern: 'rm *',
        },
      }),
    }
    await useMessageStore.getState().loadMessages(fakeClient as any, 'conv1')
    const confirm = useMessageStore.getState().pendingConfirmMap['tc1']
    expect(confirm).toBeTruthy()
    expect(confirm.command).toBe('rm -rf /tmp/x')
  })

  it('leaves pendingAsk null and pendingConfirmMap empty when pending_hitl is null', async () => {
    const fakeClient = { get: vi.fn().mockResolvedValue({ messages: [], active_run: null, pending_hitl: null }) }
    await useMessageStore.getState().loadMessages(fakeClient as any, 'conv1')
    expect(useMessageStore.getState().pendingAsk).toBeNull()
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap).length).toBe(0)
  })
})
```

- [ ] **Step 4: Run to verify FAIL**

```
cd frontend
pnpm test:run packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts
```

- [ ] **Step 5: Implement bootstrap seed**

In `frontend/packages/core/src/types/events.ts`, add `pending_hitl: PendingHitl | null` to the bootstrap response type (mirror spec §7 union).

In `frontend/packages/core/src/stores/messageStore.ts` `loadMessages` —
this is the load that ALREADY does `pendingConfirmMap: {}, pendingAsk: null`
inside its `set()` call (around line 958). The seed MUST land in the
SAME `set()` so the resets don't clobber it:

```ts
// Build seed values from bootstrap.pending_hitl FIRST.
let seedPendingAsk: PendingAsk | null = null
let seedPendingConfirmMap: Record<string, PendingConfirm> = {}

if (bootstrap.pending_hitl?.kind === 'ask_user') {
  seedPendingAsk = {
    question_id: bootstrap.pending_hitl.question_id,
    questions: bootstrap.pending_hitl.questions,
    requestedAt: Date.parse(bootstrap.pending_hitl.requested_at) || Date.now(),
    timeout_seconds: null,
    // run_id is needed by the answer-submit URL; add it as a new field on
    // PendingAsk in messageStore.ts (the live SSE path will need it too —
    // applyStreamEvent for ask_user_request must also populate run_id from
    // the current bootstrap.active_run or the event payload).
    run_id: bootstrap.pending_hitl.run_id,
  }
} else if (bootstrap.pending_hitl?.kind === 'sandbox_confirm') {
  seedPendingConfirmMap = {
    [bootstrap.pending_hitl.tool_call_id]: {
      question_id: bootstrap.pending_hitl.question_id,
      tool_call_id: bootstrap.pending_hitl.tool_call_id,
      command: bootstrap.pending_hitl.command,
      matched_pattern: bootstrap.pending_hitl.matched_pattern,
      requestedAt: Date.parse(bootstrap.pending_hitl.requested_at) || Date.now(),
      timeout_seconds: null,
      run_id: bootstrap.pending_hitl.run_id,
    },
  }
}

// Then in the existing set(): replace `pendingConfirmMap: {}, pendingAsk: null`
// with the seeds.
set({
  // ... all existing fields, but instead of:
  //   pendingConfirmMap: {},
  //   pendingAsk: null,
  // use:
  pendingConfirmMap: seedPendingConfirmMap,
  pendingAsk: seedPendingAsk,
  // ... rest unchanged
})
```

`PendingAsk` + `PendingConfirm` interfaces in messageStore.ts:62-77 need
to gain a `run_id: string` field; update `applyStreamEvent`'s
`ask_user_request` / `sandbox_confirm_request` branches (around lines 568,
612) to populate it from the live event payload (or from `currentRunId`
in the store state).

In the submit handlers (`submitAskUserAnswer`, `submitSandboxConfirm`),
on a 409 response:

- `code: "resume_in_flight"` — toast "Already submitted, refreshing…" and reload bootstrap to converge state.
- `code: "stale_answer"` — toast "Question is no longer current" and reload bootstrap.
- `code: "conversation_moved"` — same as stale_answer.
- `code: "paused_hitl"` (only on steer/start_run paths) — toast "Answer the pending question first".

On 404 `code: "no_pending"` — silently clear `pendingAsk` / the matching `pendingConfirmMap` entry (someone else answered).

- [ ] **Step 6: Composer lock**

In the composer file (found in step 1), import `useMessageStore` and
disable the input + send button when:

```ts
const hasPendingHitl =
  Object.keys(state.pendingConfirmMap).length > 0 ||
  state.pendingAsk !== null
```

Tooltip: "Answer the pending question above first."

- [ ] **Step 6: Run all frontend tests**

```
cd frontend
pnpm test:run packages/core/__tests__/stores
```
Expected: all PASS, including the new file.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/types/events.ts \
        frontend/packages/core/src/stores/messageStore.ts \
        frontend/packages/web/components/chat/Composer.tsx \
        frontend/packages/core/__tests__/stores/messageStore.bootstrapPendingHitl.test.ts
git commit -m "feat(frontend): seed pending HITL from bootstrap + lock composer"
```

---

## Task 15: Frontend — `policy_overridden` decision handling

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts` (extend resolved decision union)
- Modify: `frontend/packages/core/src/stores/messageStore.ts` (`applyStreamEvent` accepts new decision)
- Modify: `frontend/packages/web/components/chat/SandboxConfirmCard.tsx` / `AskUserCard.tsx` (inline "Skipped" note)
- Test: `frontend/packages/core/__tests__/stores/messageStore.policyOverridden.test.ts` (NEW)

- [ ] **Step 1: Write failing test**

```typescript
// frontend/packages/core/__tests__/stores/messageStore.policyOverridden.test.ts
import { describe, it, expect } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

describe('policy_overridden resolution', () => {
  it('removes the pending confirm on decision="policy_overridden"', () => {
    // Seed pendingConfirmMap with one entry (use the store's existing
    // helper or applyStreamEvent with sandbox_confirm_request).
    useMessageStore.setState({
      pendingConfirmMap: {
        tc1: {
          question_id: 'q1', tool_call_id: 'tc1',
          command: 'rm', matched_pattern: 'rm *',
          requestedAt: Date.now(), timeout_seconds: null, run_id: 'r1',
        } as any,
      },
    })
    useMessageStore.getState().applyStreamEvent({
      type: 'sandbox_confirm_resolved',
      data: {
        question_id: 'q1', tool_call_id: 'tc1',
        decision: 'policy_overridden',
        cancelled: false, timed_out: false,
        reason: 'org sandbox policy changed during pause',
      },
    } as any)
    expect(useMessageStore.getState().pendingConfirmMap['tc1']).toBeUndefined()
  })

  it('removes pendingAsk on cancelled=True + reason="policy_overridden"', () => {
    useMessageStore.setState({
      pendingAsk: {
        question_id: 'q1', questions: [],
        requestedAt: Date.now(), timeout_seconds: null, run_id: 'r1',
      } as any,
    })
    // Backend emits AskUserResolvedEvent with cancelled=True + reason rather
    // than a separate 'outcome' field (the event schema has no outcome).
    useMessageStore.getState().applyStreamEvent({
      type: 'ask_user_resolved',
      data: {
        question_id: 'q1', answers: null,
        cancelled: true, timed_out: false,
        reason: 'policy_overridden',
      },
    } as any)
    expect(useMessageStore.getState().pendingAsk).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify FAIL**

```
pnpm test:run packages/core/__tests__/stores/messageStore.policyOverridden.test.ts
```

- [ ] **Step 3: Implement**

In `events.ts`, extend `SandboxConfirmResolvedEvent.data.decision` union with `"policy_overridden"`. `AskUserResolvedEvent.data` adds an optional `reason?: string` field — no new `outcome` field, no schema divergence from the live SSE path. (The backend carries `reason: 'policy_overridden'` via the cancelled-with-reason convention, see Task 12.)

In `messageStore.ts` `applyStreamEvent` (lines 592, 621): the existing handlers already remove the entry on any resolved event, so the cleanup itself works without code change. ADD: when `decision === 'policy_overridden'` (confirm) or `reason === 'policy_overridden'` (ask), append a small inline log/system message "Skipped — org sandbox policy changed" to the message list so the user understands what happened. Use whatever inline-system-note pattern the store already has for cancellations.

In `SandboxConfirmCard.tsx` / `AskUserCard.tsx`: no card-side change if the cards disappear immediately on resolved. If the design renders a transient "resolved" state, add a `policy_overridden` branch with the same copy.

- [ ] **Step 4: Run to verify PASS**

```
pnpm test:run packages/core/__tests__/stores/messageStore.policyOverridden.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/types/events.ts \
        frontend/packages/core/src/stores/messageStore.ts \
        frontend/packages/web/components/chat/SandboxConfirmCard.tsx \
        frontend/packages/web/components/chat/AskUserCard.tsx \
        frontend/packages/core/__tests__/stores/messageStore.policyOverridden.test.ts
git commit -m "feat(frontend): handle policy_overridden resolution"
```

---

## Task 16: E2E — pause/respond happy path

**Files:**
- Create: `backend/tests/e2e/test_hitl_pause_resume.py`

- [ ] **Step 1: Write the test**

```python
# backend/tests/e2e/test_hitl_pause_resume.py
"""End-to-end HITL pause/respond. Uses the existing E2E rig
(see tests/e2e/conftest.py for backend startup pattern)."""
import asyncio
import pytest

pytestmark = pytest.mark.asyncio


async def test_ask_user_pause_then_respond(e2e_client, auth_user, conversation_id):
    """Start a turn that calls ask_user → SSE delivers ask_user_request →
    submit answer → SSE continues with follow-up assistant message →
    conversation completes."""
    # Seed a system prompt or skill that exercises ask_user. The exact
    # trigger depends on existing fixtures; consult an existing ask_user
    # E2E if any, or trigger via a prompt that asks the agent to
    # 'use ask_user to confirm X'.
    # ...
    # Wait for ask_user_request event on the SSE stream
    # POST /api/v1/ws/{ws}/conversations/{cid}/runs/{rid}/ask-user-answer
    # Wait for ask_user_resolved + next assistant message + done event
    # Assert conversation status is "completed"
```

- [ ] **Step 2: Run**

```
cd backend
uv run pytest tests/e2e/test_hitl_pause_resume.py::test_ask_user_pause_then_respond -v -s
```
Expected: PASS.

- [ ] **Step 3: Add a long-pause test**

```python
async def test_long_pause_recovers_via_db_pending(e2e_client, ...):
    """Pause; wait > Redis TTL (simulated by deleting the active key);
    submit answer → resume succeeds via claim_resume's meta-missing branch."""
    # ...
```

- [ ] **Step 4: Add a policy-change-mid-pause test**

```python
async def test_policy_change_clears_dangling_on_resume(e2e_client, ...):
    """Pause on a sandbox confirm; admin changes org policy to deny the
    same pattern; submit approve → respond reevaluates, middleware
    blocks, dangling pending cleaned, synthetic resolved event fires."""
    # ...
```

- [ ] **Step 5: Add a duplicate-submit test**

```python
async def test_concurrent_submit_one_wins(e2e_client, ...):
    """Fire two simultaneous answer POSTs → exactly one returns 200,
    the other returns 409 resume_in_flight."""
    # ...
```

- [ ] **Step 6: Run the E2E module**

```
uv run pytest tests/e2e/test_hitl_pause_resume.py -v -s
```
Expected: all PASS.

- [ ] **Step 7: Run the full backend test suite**

```
uv run pytest tests/unit tests/e2e -x -q
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/e2e/test_hitl_pause_resume.py
git commit -m "test(hitl): e2e pause/resume — happy path, long-pause, policy change, duplicate submit"
```

---

## Task 17: Scheduled-task dispatch handles `paused_hitl`

**Files:**
- Modify: `backend/cubebox/schedules/dispatch.py` (around line 110 — the `"already" in str(exc)` matcher that maps `RuntimeError` to `ConversationBusyError`)
- Test: `backend/tests/unit/test_schedules_dispatch_paused.py` (NEW)

The scheduled-task poller today catches `RuntimeError` from `start_run` and treats it as transient busy (5-minute retry up to `_max_busy_retries`). After Task 3, `start_run` raises a new `RuntimeError("Conversation ... has a pending HITL request ...; answer or cancel before starting a new turn")`. The poller will busy-retry on paused conversations until exhaustion — wasted retries on a state only the user can clear.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_schedules_dispatch_paused.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_paused_hitl_dispatch_records_paused_not_busy():
    """A paused_hitl conversation must not be busy-retried; the scheduled
    occurrence should mark itself paused/skipped (terminal for this fire),
    and the next fire happens on the normal schedule, not after 5 min."""
    from cubebox.schedules import dispatch
    # ... build minimal scheduler context; simulate start_run raising
    # RuntimeError with the new "has a pending HITL request" message.
    # Assert occurrence is marked something like "skipped_paused", NOT busy.
```

- [ ] **Step 2: Run to verify FAIL**

```
uv run pytest tests/unit/test_schedules_dispatch_paused.py -v
```

- [ ] **Step 3: Implement**

In `backend/cubebox/schedules/dispatch.py` around the existing busy-detection logic:

```python
except RuntimeError as exc:
    msg = str(exc)
    if "pending HITL request" in msg:
        # Paused conversation — user must answer first. Mark occurrence
        # as skipped-paused and move on; do NOT busy-retry.
        await record_scheduled_run_terminal_state(
            run_id=occurrence.run_id, run_status="skipped_paused",
        )
        logger.info("scheduled occurrence skipped: paused HITL on {}", conv_id)
        return
    if "already" in msg:
        raise ConversationBusyError(msg) from exc
    raise
```

The exact `record_scheduled_run_terminal_state` call shape mirrors the existing `completed` / `cancelled` cases in run_manager.py around line 2234-2245.

- [ ] **Step 4: Run to verify PASS**

```
uv run pytest tests/unit/test_schedules_dispatch_paused.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/schedules/dispatch.py backend/tests/unit/test_schedules_dispatch_paused.py
git commit -m "feat(schedules): skip paused_hitl conversations instead of busy-retry"
```

---

## Task 18: Final integration smoke + lint

- [ ] **Step 1: Run the whole backend test suite**

```
cd backend && uv run pytest -x -q
```

- [ ] **Step 2: Run the whole frontend test suite**

```
cd frontend && pnpm test:run
```

- [ ] **Step 3: Lint / type-check**

```
cd backend && uv run mypy cubebox
cd frontend && pnpm typecheck
```
Expected: zero errors.

- [ ] **Step 4: Manual smoke — launch the dev stack**

```bash
# Backend
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond/backend
uv run python main.py &

# Frontend
cd /home/chris/cubebox/.worktrees/feat/hitl-checkpointed-respond/frontend
pnpm dev
```
Visit `http://<host>:3052`, trigger an ask_user flow in a chat, close the tab, reopen after 10 minutes, answer, watch the agent continue. (Run on a remote-accessible bind per the user's memory — `0.0.0.0`.)

- [ ] **Step 5: Commit any lint/typecheck fixes**

```bash
git add -A
git commit -m "chore: address mypy/typecheck/lint follow-ups"
```

---

## Self-review checklist (for the implementer)

After all tasks are complete, before opening the PR, verify:

- [ ] `grep -rn "InMemoryChannel" backend/cubebox/streams` returns nothing.
- [ ] `grep -rn "timeout=180" backend/cubebox` returns nothing (tests/fixtures may keep 180.0 as fixture data — that's fine).
- [ ] `grep -rn "dispatch_ask_user_answer\|dispatch_hitl_answer" backend/cubebox` returns nothing.
- [ ] `grep -rn "paused_hitl" backend/cubebox frontend/packages` returns hits in all the expected files: `run_events.py`, `run_manager.py`, `hitl_resume.py`, conversation bootstrap, messageStore.
- [ ] Conversation bootstrap response shape matches spec §7 `PendingHitl` union exactly.
- [ ] Manually verified pause/answer flow on the worktree's frontend.
- [ ] Codex review loop (`.claude/skills/pr-codex-review-loop/`) run after pushing the PR.
