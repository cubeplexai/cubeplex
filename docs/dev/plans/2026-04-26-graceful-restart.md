# Graceful Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On `SIGTERM` / `SIGINT` / lifespan shutdown, drain in-flight LangGraph runs before exit, route new run starts to `503` while draining, split health probes for k8s, and add inline stale-run detection to recover from non-graceful death.

**Architecture:** Process-level `DrainState` gates the run-start surface via an outermost ASGI middleware. `RunManager.drain()` waits on a `_tasks_empty` event with a long timeout (default 3600s); the existing per-task cancel path handles forced shutdown. Run metadata gains a wall-clock `last_event_at` heartbeat; `bootstrap` and stream subscribe atomically clear orphaned `active_run` keys via Lua CAS and surface `last_run_status: "stale"` so the frontend can render a failure bubble.

**Tech Stack:** FastAPI ASGI middleware, asyncio signal handlers (`loop.add_signal_handler`), Redis Lua scripts (atomic CAS), pytest-asyncio with real Redis.

**Spec:** `docs/superpowers/specs/2026-04-25-graceful-restart-design.md`

---

## File Structure

**New files:**
- `backend/cubeplex/lifecycle/__init__.py` — package marker
- `backend/cubeplex/lifecycle/drain.py` — `DrainState` class (process-level state machine)
- `backend/cubeplex/api/middleware/drain.py` — `DrainMiddleware` ASGI middleware
- `backend/tests/unit/test_drain_state.py` — `DrainState` unit tests
- `backend/tests/e2e/test_graceful_restart.py` — drain + stale detection E2E tests

**Modified backend files:**
- `backend/cubeplex/streams/run_events.py` — add `last_event_at` field, extend `_APPEND_EVENT_LUA`, add `_MARK_STALE_LUA` + `mark_run_stale()` helper, add `is_stale_meta()` predicate
- `backend/cubeplex/streams/run_manager.py` — rename `shutdown()` → `cancel_all()`, add `drain()`, maintain `_tasks_empty` event, pass `last_event_at` to `append_run_event`
- `backend/cubeplex/api/app.py` — register signal handlers in lifespan, replace `shutdown()` with `enter_draining() + drain()`, register `DrainMiddleware`, mount health router
- `backend/cubeplex/api/routes/health.py` — split `/health/live` and `/health/ready`, drop `/health`
- `backend/cubeplex/api/routes/v1/conversations.py` — bootstrap stale check + `last_run_status`; stream subscribe stale check + synthetic error event
- `backend/config.yaml`, `config.development.yaml`, `config.production.yaml`, `config.test.yaml` — add `lifecycle.*` keys

**Modified frontend files:**
- `frontend/packages/core/src/api/types.ts` — `last_run_status` on bootstrap
- `frontend/packages/core/src/stores/messageStore.ts` — store stale flag from bootstrap
- `frontend/packages/web/components/chat/MessageList.tsx` — render stale-run error bubble

---

## Task 1: Add `last_event_at` to RunMeta and Lua heartbeat

**Files:**
- Modify: `backend/cubeplex/streams/run_events.py`
- Modify: `backend/cubeplex/streams/run_manager.py:276-286` (`_append_event`)
- Test: `backend/tests/e2e/test_graceful_restart.py`

The `last_event_at` ISO timestamp is stamped on every event append by `_APPEND_EVENT_LUA`. This enables stale detection later. Pure additive change — readers that don't know the field still work.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/e2e/test_graceful_restart.py
"""E2E tests for graceful restart drain + stale-run detection."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from cubeplex.config import config as _cubeplex_config
from cubeplex.streams.run_events import (
    append_run_event,
    create_run,
    get_run_meta,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
async def redis_client():
    client = Redis.from_url(
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    yield client
    await client.aclose()


async def test_append_run_event_stamps_last_event_at(redis_client: Redis) -> None:
    prefix = "test_graceful"
    run_id = "run-heartbeat-1"
    conv_id = "conv-heartbeat-1"
    started = datetime.now(UTC).isoformat()

    meta = await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=started,
        ttl_seconds=60,
    )
    assert meta is not None

    before = datetime.now(UTC)
    await append_run_event(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        payload={"type": "status", "data": {"phase": "test"}},
        ttl_seconds=60,
        maxlen=100,
    )
    after = datetime.now(UTC)

    fresh_meta = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh_meta is not None
    assert fresh_meta.last_event_at is not None
    parsed = datetime.fromisoformat(fresh_meta.last_event_at)
    assert before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1)
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py::test_append_run_event_stamps_last_event_at -v`
Expected: FAIL — `RunMeta` has no `last_event_at` attribute.

- [ ] **Step 3: Add `last_event_at` to `RunMeta` dataclass and parser**

In `backend/cubeplex/streams/run_events.py`, modify the `RunMeta` dataclass:

```python
@dataclass(slots=True)
class RunMeta:
    """Metadata for a single run."""

    run_id: str
    conversation_id: str
    status: str
    started_at: str
    user_message: str | None = None
    first_event_id: str | None = None
    last_event_id: str | None = None
    last_event_at: str | None = None
```

Modify `_meta_from_hash`:

```python
def _meta_from_hash(raw: dict[str, str]) -> RunMeta | None:
    if not raw:
        return None
    return RunMeta(
        run_id=raw["run_id"],
        conversation_id=raw["conversation_id"],
        status=raw["status"],
        started_at=raw["started_at"],
        user_message=raw.get("user_message"),
        first_event_id=raw.get("first_event_id"),
        last_event_id=raw.get("last_event_id"),
        last_event_at=raw.get("last_event_at"),
    )
```

- [ ] **Step 4: Extend `_APPEND_EVENT_LUA` to stamp `last_event_at`**

In the same file, replace the existing `_APPEND_EVENT_LUA` block:

```python
_APPEND_EVENT_LUA = """
local eid = redis.call(
  'XADD', KEYS[1], 'MAXLEN', '~', tonumber(ARGV[3]), '*', 'payload', ARGV[1]
)
redis.call('HSET', KEYS[2], 'last_event_id', eid, 'last_event_at', ARGV[5])
redis.call('HSETNX', KEYS[2], 'first_event_id', eid)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[2]))
if redis.call('GET', KEYS[3]) == ARGV[4] then
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[2]))
end
return eid
"""
```

- [ ] **Step 5: Update `append_run_event` to compute and pass `last_event_at`**

Replace the existing `append_run_event` function body with:

```python
async def append_run_event(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    maxlen: int,
) -> str:
    """Append an event payload and update event bounds in a single call.

    Stamps a wall-clock ``last_event_at`` heartbeat so stale-run detection
    can spot dead workers. Also heartbeats the active-run lock for this
    conversation so runs longer than ``ttl_seconds`` don't drop the lock
    mid-execution.
    """
    from datetime import UTC, datetime

    last_event_at = datetime.now(UTC).isoformat()
    return cast(
        str,
        await redis.eval(  # type: ignore[misc]
            _APPEND_EVENT_LUA,
            3,
            _run_events_key(prefix, run_id),
            _run_meta_key(prefix, run_id),
            _active_run_key(prefix, conversation_id),
            json.dumps(payload),
            str(ttl_seconds),
            str(maxlen),
            run_id,
            last_event_at,
        ),
    )
```

- [ ] **Step 6: Run test, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py::test_append_run_event_stamps_last_event_at -v`
Expected: PASS.

- [ ] **Step 7: Confirm existing streaming tests still pass**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py -v`
Expected: all PASS (no regression in append behavior).

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/streams/run_events.py backend/tests/e2e/test_graceful_restart.py
git commit -m "feat(streams): heartbeat last_event_at on run event append"
```

---

## Task 2: Lifecycle configuration keys

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml`
- Modify: `backend/config.production.yaml`
- Modify: `backend/config.test.yaml`

Adds the three knobs the runtime reads. Default values match the spec.

- [ ] **Step 1: Add lifecycle block to base `config.yaml`**

In `backend/config.yaml`, append a top-level block (preserve existing keys, add this section):

```yaml
lifecycle:
  graceful_drain_timeout_seconds: 3600
  dev_double_signal_force_exit: true
  stale_run_threshold_seconds: 120
```

- [ ] **Step 2: Override in `config.test.yaml` for fast tests**

In `backend/config.test.yaml`, add or merge:

```yaml
lifecycle:
  graceful_drain_timeout_seconds: 5
  dev_double_signal_force_exit: false
  stale_run_threshold_seconds: 1
```

The 5-second timeout keeps drain tests fast; 1-second stale threshold makes
stale tests deterministic without sleeping.

- [ ] **Step 3: Confirm dev and prod configs inherit base**

Inspect `backend/config.development.yaml` and `backend/config.production.yaml`. If either explicitly sets unrelated lifecycle/runtime sections, leave them. If they need lifecycle overrides specific to that env, add an empty `lifecycle: {}` to make the inheritance explicit. Otherwise no change is needed.

- [ ] **Step 4: Verify config loads**

Run: `cd backend && uv run python -c "from cubeplex.config import config; print(config.get('lifecycle.graceful_drain_timeout_seconds')); print(config.get('lifecycle.stale_run_threshold_seconds'))"`
Expected: prints `3600` then `120`.

- [ ] **Step 5: Commit**

```bash
git add backend/config.yaml backend/config.test.yaml backend/config.development.yaml backend/config.production.yaml
git commit -m "feat(config): add lifecycle.* drain and stale-run keys"
```

---

## Task 3: `DrainState` lifecycle module

**Files:**
- Create: `backend/cubeplex/lifecycle/__init__.py`
- Create: `backend/cubeplex/lifecycle/drain.py`
- Test: `backend/tests/unit/test_drain_state.py`

Process-level state machine. Idempotent transitions. No async — purely synchronous flag flipping. The async waiting lives in `RunManager.drain()`.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_drain_state.py
"""DrainState transitions."""

from __future__ import annotations

from cubeplex.lifecycle.drain import DrainState


def test_initial_state_is_accepting() -> None:
    state = DrainState()
    assert state.is_accepting()
    assert not state.is_draining()


def test_enter_draining_flips_flag() -> None:
    state = DrainState()
    state.enter_draining()
    assert not state.is_accepting()
    assert state.is_draining()


def test_enter_draining_is_idempotent() -> None:
    state = DrainState()
    state.enter_draining()
    state.enter_draining()
    assert state.is_draining()
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/unit/test_drain_state.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the package marker**

Create `backend/cubeplex/lifecycle/__init__.py`:

```python
"""Process-level lifecycle primitives (drain, stale detection)."""

from cubeplex.lifecycle.drain import DrainState

__all__ = ["DrainState"]
```

- [ ] **Step 4: Implement `DrainState`**

Create `backend/cubeplex/lifecycle/drain.py`:

```python
"""Process-level drain state machine.

The state machine is read by request handlers (drain middleware, health
probes) and written by signal handlers + the FastAPI lifespan shutdown
hook. It does not own any async waiting — that lives in
``RunManager.drain()``.
"""

from __future__ import annotations

from typing import Literal

State = Literal["accepting", "draining"]


class DrainState:
    """Single-process drain flag.

    Transitions:
        accepting -> draining (via enter_draining; idempotent)

    The 'exiting' phase from the design doc is a property of the lifespan
    shutdown sequence, not a state we observe at runtime. Once
    ``RunManager.drain()`` returns, the process tears down.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state: State = "accepting"

    def is_accepting(self) -> bool:
        return self._state == "accepting"

    def is_draining(self) -> bool:
        return self._state == "draining"

    def enter_draining(self) -> None:
        self._state = "draining"
```

- [ ] **Step 5: Run test, verify PASS**

Run: `cd backend && uv run pytest tests/unit/test_drain_state.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/lifecycle/ backend/tests/unit/test_drain_state.py
git commit -m "feat(lifecycle): introduce DrainState"
```

---

## Task 4: `RunManager.drain()` and `cancel_all()` rename

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py:222-274` (constructor + shutdown method)
- Test: `backend/tests/e2e/test_graceful_restart.py`

Maintains an `asyncio.Event` set whenever `_tasks` becomes empty. `drain(timeout)` waits on it with a periodic 30s progress logger. Timeout falls through to the existing cancel path.

- [ ] **Step 1: Write failing test for empty-tasks fast path**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
from types import SimpleNamespace
from cubeplex.streams.run_manager import RunManager


def _make_run_manager(redis_client: Redis) -> RunManager:
    app = SimpleNamespace(state=SimpleNamespace())
    return RunManager(
        app=app,
        redis=redis_client,
        key_prefix="test_drain",
        run_event_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_no_tasks(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)
    start = asyncio.get_event_loop().time()
    await rm.drain(timeout_seconds=10.0)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_task(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)

    async def slow() -> None:
        await asyncio.sleep(0.3)

    task = asyncio.create_task(slow(), name="run:slow-1")
    rm._tasks["slow-1"] = task
    task.add_done_callback(lambda _: rm._on_task_done("slow-1"))

    start = asyncio.get_event_loop().time()
    await rm.drain(timeout_seconds=5.0)
    elapsed = asyncio.get_event_loop().time() - start
    assert 0.25 < elapsed < 1.5
    assert "slow-1" not in rm._tasks


@pytest.mark.asyncio
async def test_drain_timeout_cancels_residual(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)

    async def forever() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(forever(), name="run:forever")
    rm._tasks["forever"] = task
    task.add_done_callback(lambda _: rm._on_task_done("forever"))

    await rm.drain(timeout_seconds=0.2)
    # cancel_all path completed: task is done (cancelled) and removed.
    assert task.cancelled() or task.done()
```

- [ ] **Step 2: Run tests, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k drain -v`
Expected: FAIL — `RunManager.drain` does not exist; `_on_task_done` does not exist.

- [ ] **Step 3: Modify `RunManager.__init__` to add the empty event and a helper**

In `backend/cubeplex/streams/run_manager.py`, replace the constructor block (around line 208-222) with:

```python
    def __init__(
        self,
        *,
        app: FastAPI,
        redis: Redis,
        key_prefix: str,
        run_event_ttl_seconds: int,
        run_stream_max_events: int = 1000000,
    ) -> None:
        self._app = app
        self._redis = redis
        self._key_prefix = key_prefix
        self._run_event_ttl_seconds = run_event_ttl_seconds
        self._run_stream_max_events = run_stream_max_events
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tasks_empty: asyncio.Event = asyncio.Event()
        self._tasks_empty.set()

    def _on_task_done(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        if not self._tasks:
            self._tasks_empty.set()
```

- [ ] **Step 4: Wire `_on_task_done` into the existing task callback**

In `RunManager.start_run`, replace the existing callback registration:

```python
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
```

with:

```python
        task.add_done_callback(lambda _: self._on_task_done(run_id))
```

Also clear the empty event when a new task is registered. Insert immediately before `self._tasks[run_id] = task`:

```python
        self._tasks_empty.clear()
```

- [ ] **Step 5: Rename `shutdown` → `cancel_all`, add `drain`**

Replace the existing `shutdown` method (around line 267-274) with both methods:

```python
    async def cancel_all(self) -> None:
        """Cancel every in-flight run task. Forced shutdown path."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def drain(self, timeout_seconds: float) -> None:
        """Wait for in-flight runs to finish, then return.

        On timeout, cancels residual tasks via ``cancel_all`` (which lets
        the per-task cancel path mark status=cancelled and write an
        ``error`` event before the lock is released).

        Logs a progress line every 30 seconds while waiting.
        """
        if self._tasks_empty.is_set():
            return

        progress_task = asyncio.create_task(self._log_drain_progress())
        try:
            await asyncio.wait_for(self._tasks_empty.wait(), timeout=timeout_seconds)
        except TimeoutError:
            logger.warning(
                "Drain timeout after {}s, cancelling {} residual run(s)",
                timeout_seconds,
                len(self._tasks),
            )
            await self.cancel_all()
        finally:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task

    async def _log_drain_progress(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                if self._tasks:
                    logger.info("Still draining: {} run(s) remaining", len(self._tasks))
        except asyncio.CancelledError:
            return
```

- [ ] **Step 6: Run drain tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k drain -v`
Expected: 3 PASS.

- [ ] **Step 7: Update existing call sites of `shutdown()`**

`grep -rn "run_manager.shutdown\|\.shutdown()" backend/cubeplex/ backend/tests/ | grep -v node_modules`

The lifespan in `backend/cubeplex/api/app.py:213` calls `run_manager.shutdown()`. Update to `run_manager.cancel_all()` for now — Task 7 will replace this with the proper drain call.

```python
    if run_manager is not None:
        await run_manager.cancel_all()
```

- [ ] **Step 8: Run full streaming suite, verify no regression**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/cubeplex/api/app.py backend/tests/e2e/test_graceful_restart.py
git commit -m "feat(run-manager): add drain() with task-empty event, rename shutdown to cancel_all"
```

---

## Task 5: `DrainMiddleware`

**Files:**
- Create: `backend/cubeplex/api/middleware/drain.py`
- Test: `backend/tests/e2e/test_graceful_restart.py`

Outermost ASGI middleware. Returns 503 on `POST /api/v1/ws/{ws}/conversations/{cid}/messages` when draining. Path-based check, no regex; method + segment count + tail equality is enough.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
from cubeplex.api.middleware.drain import DrainMiddleware
from cubeplex.lifecycle.drain import DrainState


@pytest.mark.asyncio
async def test_drain_middleware_passthrough_when_accepting() -> None:
    state = DrainState()
    received_scope: dict[str, object] = {}

    async def downstream(scope, receive, send):
        received_scope["called"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = DrainMiddleware(downstream, drain_state=state)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ws/ws-1/conversations/c-1/messages",
    }
    await mw(scope, receive, send)
    assert received_scope.get("called") is True
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_drain_middleware_blocks_new_run_when_draining() -> None:
    state = DrainState()
    state.enter_draining()

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not be called during drain")

    mw = DrainMiddleware(downstream, drain_state=state)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ws/ws-1/conversations/c-1/messages",
    }
    await mw(scope, receive, send)
    assert sent[0]["status"] == 503
    headers = dict(sent[0]["headers"])
    assert headers[b"retry-after"] == b"5"


@pytest.mark.asyncio
async def test_drain_middleware_passes_through_non_run_paths_when_draining() -> None:
    state = DrainState()
    state.enter_draining()

    called = {"yes": False}

    async def downstream(scope, receive, send):
        called["yes"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = DrainMiddleware(downstream, drain_state=state)

    async def receive():
        return {"type": "http.request"}

    async def send(_msg):
        pass

    # SSE subscription should pass through
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/ws/ws-1/conversations/c-1/runs/r-1/stream",
    }
    await mw(scope, receive, send)
    assert called["yes"] is True
```

- [ ] **Step 2: Run tests, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k drain_middleware -v`
Expected: FAIL — `DrainMiddleware` does not exist.

- [ ] **Step 3: Implement `DrainMiddleware`**

Create `backend/cubeplex/api/middleware/drain.py`:

```python
"""Reject new run starts with 503 while the process is draining.

This middleware is registered last in ``create_app`` so it runs as the
outermost wrapper on the request path: a draining server should refuse new
runs before doing CSRF or identity work.

Only the run-start surface is gated. SSE subscription, bootstrap, auth,
and health probes pass through unchanged so existing clients keep working
during drain.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from cubeplex.lifecycle.drain import DrainState

_RETRY_AFTER_SECONDS = "5"
_BLOCKED_BODY = json.dumps(
    {"error": {"code": "draining", "message": "Server is draining; retry."}}
).encode()


def _is_run_start(scope: Scope) -> bool:
    """Match POST /api/v1/ws/{ws}/conversations/{cid}/messages exactly."""
    if scope.get("method") != "POST":
        return False
    path = scope.get("path", "")
    if not path.startswith("/api/v1/ws/"):
        return False
    segments = path.strip("/").split("/")
    # ['api', 'v1', 'ws', '{ws}', 'conversations', '{cid}', 'messages']
    return (
        len(segments) == 7
        and segments[4] == "conversations"
        and segments[6] == "messages"
    )


class DrainMiddleware:
    def __init__(self, app: ASGIApp, *, drain_state: DrainState) -> None:
        self.app = app
        self._state = drain_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._state.is_draining():
            await self.app(scope, receive, send)
            return

        if not _is_run_start(scope):
            await self.app(scope, receive, send)
            return

        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"retry-after", _RETRY_AFTER_SECONDS.encode()),
                    (b"connection", b"close"),
                    (b"content-type", b"application/json"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BLOCKED_BODY})
```

- [ ] **Step 4: Run tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k drain_middleware -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/middleware/drain.py backend/tests/e2e/test_graceful_restart.py
git commit -m "feat(api): DrainMiddleware refuses new run starts while draining"
```

---

## Task 6: Health route split

**Files:**
- Modify: `backend/cubeplex/api/routes/health.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py` (add health export if missing)
- Test: `backend/tests/e2e/test_graceful_restart.py`

Replaces the single `/health` with `/health/live` (always 200) and `/health/ready` (503 while draining). The legacy `/health` is dropped per the design doc.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
import httpx


@pytest.mark.asyncio
async def test_health_live_always_200(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_200_when_accepting(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health/ready")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_ready_503_when_draining(
    memory_client: httpx.AsyncClient, app_state_drain: DrainState
) -> None:
    app_state_drain.enter_draining()
    try:
        resp = await memory_client.get("/health/ready")
        assert resp.status_code == 503
        # Liveness must remain 200 — k8s should not kill the pod during drain.
        live_resp = await memory_client.get("/health/live")
        assert live_resp.status_code == 200
    finally:
        # Reset for subsequent tests in the session.
        app_state_drain._state = "accepting"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_legacy_health_removed(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health")
    assert resp.status_code == 404
```

The `app_state_drain` fixture will be added in Task 7 (lifespan wiring). For now, these tests are expected to fail at collection or at fixture lookup; that's fine.

- [ ] **Step 2: Run tests, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k health -v`
Expected: FAIL — endpoints don't exist (or `app_state_drain` fixture missing).

- [ ] **Step 3: Replace the health router**

Replace the entire contents of `backend/cubeplex/api/routes/health.py` with:

```python
"""Health probes split for k8s.

- ``/health/live`` is the liveness probe. Always 200 while the process is up.
  Must not flip during drain or k8s will kill the pod before drain completes.
- ``/health/ready`` is the readiness probe. 503 while draining so the load
  balancer stops sending new traffic to this pod.
"""

from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, str]:
    drain_state = getattr(request.app.state, "drain_state", None)
    if drain_state is not None and drain_state.is_draining():
        response.status_code = 503
        return {"status": "draining"}
    return {"status": "ok"}
```

- [ ] **Step 4: Export the router and mount it**

Confirm `backend/cubeplex/api/routes/__init__.py` does not need changes — it's just the package marker. The router is mounted directly from `app.py`.

In `backend/cubeplex/api/app.py`, inside `create_app`, add the import and mount near the other `include_router` calls (around line 274-285):

```python
    from cubeplex.api.routes.health import router as health_router

    app.include_router(health_router)
```

(No `/api/v1` prefix — `/health/*` is conventionally unversioned.)

- [ ] **Step 5: Run live + legacy tests, verify partial PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "health_live_always or legacy_health" -v`
Expected: both PASS. The two readiness tests stay failing until Task 7 wires `app.state.drain_state`.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/health.py backend/cubeplex/api/app.py
git commit -m "feat(health): split /health/live and /health/ready, drop /health"
```

---

## Task 7: Lifespan integration (signal handlers + drain + middleware)

**Files:**
- Modify: `backend/cubeplex/api/app.py`
- Test: `backend/tests/e2e/test_graceful_restart.py`

Wires everything: install signal handlers, register `DrainMiddleware`, replace `cancel_all()` in lifespan shutdown with `enter_draining() + drain(timeout)`, expose `app.state.drain_state`.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
@pytest.fixture
async def app_state_drain(memory_client: httpx.AsyncClient) -> DrainState:
    """Pull the live DrainState from the app the test client targets."""
    app = memory_client._transport.app  # type: ignore[attr-defined]
    state = getattr(app.state, "drain_state", None)
    assert isinstance(state, DrainState), "lifespan did not install drain_state"
    return state


@pytest.mark.asyncio
async def test_post_messages_returns_503_when_draining(
    memory_client: httpx.AsyncClient,
    app_state_drain: DrainState,
) -> None:
    # Create a conversation first while still accepting.
    create_resp = await memory_client.post(
        "/api/v1/ws/default-ws/conversations", params={"title": "drain-test"}
    )
    assert create_resp.status_code == 200
    conv_id = create_resp.json()["id"]

    app_state_drain.enter_draining()
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
            json={"content": "hi"},
        )
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
    finally:
        app_state_drain._state = "accepting"  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k post_messages_returns_503 -v`
Expected: FAIL — middleware not registered, drain_state not on app.state.

- [ ] **Step 3: Create `DrainState` in `create_app` (so middleware can capture it)**

In `backend/cubeplex/api/app.py`, inside `create_app`, BEFORE the existing `app.add_middleware(...)` calls (around line 252), insert:

```python
    from cubeplex.lifecycle.drain import DrainState

    app.state.drain_state = DrainState()
```

`add_middleware` runs at app-construction time, before the lifespan starts. The middleware needs the same `DrainState` instance the lifespan and signal handlers will write to, so we create it once here.

- [ ] **Step 4: Register `DrainMiddleware` last (outermost)**

In the same `create_app`, AFTER all existing `app.add_middleware(...)` calls (around line 260), add:

```python
    from cubeplex.api.middleware.drain import DrainMiddleware

    app.add_middleware(DrainMiddleware, drain_state=app.state.drain_state)
```

Last-registered = outermost on the request path: drain refuses new runs before any other middleware does work.

- [ ] **Step 5: Install signal handlers in lifespan startup**

In the lifespan function, immediately after `log.init()` (around line 29-30), insert:

```python
    import asyncio
    import os
    import signal as _signal

    drain_state = _app.state.drain_state
    loop = asyncio.get_running_loop()
    force_exit_enabled = config.get("lifecycle.dev_double_signal_force_exit", True)
    _signal_seen: dict[str, bool] = {"first": False}

    def _on_signal(signame: str) -> None:
        if not _signal_seen["first"]:
            _signal_seen["first"] = True
            logger.info("{} received — entering drain mode", signame)
            drain_state.enter_draining()
            return
        if force_exit_enabled:
            logger.warning("Second {} received — force exiting", signame)
            # Schedule cancel_all and exit immediately. We do not block on
            # cancel_all because the developer pressed Ctrl-C twice
            # precisely to skip the wait.
            rm = getattr(_app.state, "run_manager", None)
            if rm is not None:
                asyncio.ensure_future(rm.cancel_all())
            os._exit(130)

    for sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal, sig.name)
        except NotImplementedError:
            # Non-POSIX (Windows): no graceful drain via signal there, but
            # the lifespan-shutdown path still drains on uvicorn reload.
            logger.debug("Signal {} not installable on this platform", sig.name)
```

- [ ] **Step 6: Replace `cancel_all()` with `drain()` in lifespan shutdown**

In the same file, in the shutdown section (around line 211-215), replace:

```python
    if run_manager is not None:
        await run_manager.cancel_all()
```

with:

```python
    if run_manager is not None:
        # Idempotent: if a signal already flipped the state, this is a no-op.
        # Covers uvicorn reload and other paths that don't deliver a signal.
        _app.state.drain_state.enter_draining()
        drain_timeout = config.get("lifecycle.graceful_drain_timeout_seconds", 3600)
        await run_manager.drain(timeout_seconds=float(drain_timeout))
```

- [ ] **Step 7: Run targeted tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -v`
Expected: all `health_*` and `post_messages_returns_503` tests PASS.

- [ ] **Step 8: Run full streaming suite, verify no regression**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py tests/e2e/test_conversations.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/api/app.py backend/tests/e2e/test_graceful_restart.py
git commit -m "feat(api): wire DrainState, signal handlers, drain on lifespan shutdown"
```

---

## Task 8: Stale-run detection (backend)

**Files:**
- Modify: `backend/cubeplex/streams/run_events.py` — add `_MARK_STALE_LUA`, `mark_run_stale()`, `is_stale_meta()`
- Modify: `backend/cubeplex/api/routes/v1/conversations.py` — bootstrap + stream stale handling
- Test: `backend/tests/e2e/test_graceful_restart.py`

Bootstrap and stream subscribe both check `is_stale_meta`, run `mark_run_stale` (Lua-atomic), and surface the failure. Bootstrap returns `last_run_status: "stale"`; stream emits a synthetic `error` event with `error_code: "run_stale"` and closes.

- [ ] **Step 1: Write failing tests for the helper layer**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
from cubeplex.streams.run_events import (
    is_stale_meta,
    mark_run_stale,
    set_active_run,
)


def test_is_stale_meta_detects_stale_running() -> None:
    # Helper accepts the parsed RunMeta, computes locally — no Redis.
    from datetime import UTC, datetime, timedelta

    from cubeplex.streams.run_events import RunMeta

    now = datetime.now(UTC)
    fresh = RunMeta(
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=5)).isoformat(),
    )
    stale = RunMeta(
        run_id="r2",
        conversation_id="c2",
        status="running",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=300)).isoformat(),
    )
    completed = RunMeta(
        run_id="r3",
        conversation_id="c3",
        status="completed",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=300)).isoformat(),
    )

    assert not is_stale_meta(fresh, threshold_seconds=120, now=now)
    assert is_stale_meta(stale, threshold_seconds=120, now=now)
    # Completed runs are never stale, regardless of age.
    assert not is_stale_meta(completed, threshold_seconds=120, now=now)


@pytest.mark.asyncio
async def test_mark_run_stale_clears_active_and_sets_status(redis_client: Redis) -> None:
    from datetime import UTC, datetime

    from cubeplex.streams.run_events import (
        create_run,
        get_active_run,
        get_run_meta,
    )

    prefix = "test_stale_mark"
    run_id = "r-stale-1"
    conv_id = "c-stale-1"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    await mark_run_stale(
        redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id
    )

    fresh = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh is not None
    assert fresh.status == "stale"
    active = await get_active_run(
        redis_client, prefix=prefix, conversation_id=conv_id
    )
    assert active is None


@pytest.mark.asyncio
async def test_mark_run_stale_is_idempotent(redis_client: Redis) -> None:
    from datetime import UTC, datetime

    from cubeplex.streams.run_events import create_run, get_run_meta

    prefix = "test_stale_idem"
    run_id = "r-stale-2"
    conv_id = "c-stale-2"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    await mark_run_stale(
        redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id
    )
    # Second call: status already stale, active_run already cleared — no-op.
    await mark_run_stale(
        redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id
    )
    fresh = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh is not None
    assert fresh.status == "stale"
```

- [ ] **Step 2: Run helper tests, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "is_stale_meta or mark_run_stale" -v`
Expected: FAIL — symbols not imported.

- [ ] **Step 3: Add `_MARK_STALE_LUA`, `mark_run_stale`, `is_stale_meta`**

In `backend/cubeplex/streams/run_events.py`, after the existing `_APPEND_EVENT_LUA` block, add:

```python
# Mark a run as stale and clear the active-run lock if it still points at it.
# KEYS[1] = meta_key, KEYS[2] = active_key
# ARGV[1] = expected_run_id
_MARK_STALE_LUA = """
if redis.call('HGET', KEYS[1], 'status') == 'running' then
  redis.call('HSET', KEYS[1], 'status', 'stale')
end
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[2])
end
return 1
"""
```

After the existing `expire_run_data` function, append:

```python
async def mark_run_stale(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
) -> None:
    """Atomically mark a run stale and release its active-run lock if held.

    Idempotent: a no-op when status is already non-running and the active
    key no longer points at this run.
    """
    await redis.eval(  # type: ignore[misc]
        _MARK_STALE_LUA,
        2,
        _run_meta_key(prefix, run_id),
        _active_run_key(prefix, conversation_id),
        run_id,
    )


def is_stale_meta(
    meta: RunMeta,
    *,
    threshold_seconds: int,
    now: "datetime | None" = None,
) -> bool:
    """A run is stale when status='running' and last_event_at is too old."""
    from datetime import UTC, datetime

    if meta.status != "running":
        return False
    if not meta.last_event_at:
        return False
    current = now or datetime.now(UTC)
    last = datetime.fromisoformat(meta.last_event_at)
    return (current - last).total_seconds() > threshold_seconds
```

(Add the matching `from datetime import datetime` to the type-only block at the top of the module if mypy complains. Otherwise the inline import is sufficient.)

- [ ] **Step 4: Run helper tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "is_stale_meta or mark_run_stale" -v`
Expected: 3 PASS.

- [ ] **Step 5: Write failing tests for the route layer**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
@pytest.mark.asyncio
async def test_bootstrap_clears_stale_run_and_sets_last_run_status(
    memory_client: httpx.AsyncClient, redis_client: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    from cubeplex.streams.run_events import _APPEND_EVENT_LUA  # noqa
    from cubeplex.streams.run_events import create_run, _active_run_key

    create_resp = await memory_client.post(
        "/api/v1/ws/default-ws/conversations", params={"title": "stale-test"}
    )
    assert create_resp.status_code == 200
    conv_id = create_resp.json()["id"]

    # The app under test uses prefix "test:test" (env=test). Reach it from app.state.
    app = memory_client._transport.app  # type: ignore[attr-defined]
    prefix = app.state.redis_key_prefix
    run_id = "stale-run-1"
    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()

    # Plant a fake active run with an old last_event_at.
    meta = await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=long_ago,
        ttl_seconds=120,
    )
    assert meta is not None
    # Stamp last_event_at directly (no real append, so we set the hash field).
    await redis_client.hset(  # type: ignore[misc]
        f"{prefix}:run_meta:v2:{run_id}", "last_event_at", long_ago
    )

    resp = await memory_client.get(
        f"/api/v1/ws/default-ws/conversations/{conv_id}/bootstrap"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_run"] is None
    assert body["last_run_status"] == "stale"

    # Active key cleared.
    active = await redis_client.get(_active_run_key(prefix, conv_id))
    assert active is None


@pytest.mark.asyncio
async def test_stream_subscribe_emits_stale_error_for_dead_run(
    memory_client: httpx.AsyncClient, redis_client: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    from cubeplex.streams.run_events import create_run

    create_resp = await memory_client.post(
        "/api/v1/ws/default-ws/conversations", params={"title": "stale-stream"}
    )
    conv_id = create_resp.json()["id"]

    app = memory_client._transport.app  # type: ignore[attr-defined]
    prefix = app.state.redis_key_prefix
    run_id = "stale-run-2"
    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=long_ago,
        ttl_seconds=120,
    )
    await redis_client.hset(  # type: ignore[misc]
        f"{prefix}:run_meta:v2:{run_id}", "last_event_at", long_ago
    )

    async with memory_client.stream(
        "GET",
        f"/api/v1/ws/default-ws/conversations/{conv_id}/runs/{run_id}/stream",
    ) as resp:
        body = await resp.aread()
    text = body.decode()
    # SSE chunks are concatenated; find any data: line that contains run_stale.
    assert "run_stale" in text
```

- [ ] **Step 6: Run route tests, verify FAIL**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "bootstrap_clears_stale or stream_subscribe_emits_stale" -v`
Expected: FAIL — bootstrap doesn't return `last_run_status`; stream doesn't emit synthetic error for stale.

- [ ] **Step 7: Wire stale detection into bootstrap**

In `backend/cubeplex/api/routes/v1/conversations.py`:

Add the import at the top with the other run_events imports:

```python
from cubeplex.streams.run_events import (
    get_active_run,
    get_latest_event_id,
    get_run_meta,
    is_stale_meta,
    iter_run_events,
    mark_run_stale,
    read_run_events_after,
)
```

Replace the bootstrap function body (around line 520-564) with:

```python
@router.get("/{conversation_id}/bootstrap")
async def get_conversation_bootstrap(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Return history baseline plus active run metadata."""
    from cubeplex.config import config as _cfg

    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    history = await _get_history_messages(raw_request, conversation_id)
    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )

    last_run_status: str | None = None
    if active_run is not None:
        threshold = int(_cfg.get("lifecycle.stale_run_threshold_seconds", 120))
        if is_stale_meta(active_run, threshold_seconds=threshold):
            await mark_run_stale(
                rds.client,
                prefix=rds.key_prefix,
                run_id=active_run.run_id,
                conversation_id=conversation_id,
            )
            active_run = None
            last_run_status = "stale"

    active_run_payload: dict[str, Any] | None = None
    if active_run is not None:
        active_run_payload = {
            "run_id": active_run.run_id,
            "status": active_run.status,
            "user_message": active_run.user_message,
            "last_event_id": active_run.last_event_id,
            "started_at": active_run.started_at,
        }

    return {
        "messages": history["messages"],
        "total": history["total"],
        "active_run": active_run_payload,
        "last_run_status": last_run_status,
    }
```

- [ ] **Step 8: Wire stale detection into stream subscribe**

In the same file, find `stream_run` (around line 572). Locate the block that fetches `run_meta`:

```python
    run_meta = await get_run_meta(rds.client, prefix=rds.key_prefix, run_id=run_id)
    if run_meta is None or run_meta.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
```

Immediately after this block, insert:

```python
    from cubeplex.config import config as _cfg

    threshold = int(_cfg.get("lifecycle.stale_run_threshold_seconds", 120))
    if is_stale_meta(run_meta, threshold_seconds=threshold):
        await mark_run_stale(
            rds.client,
            prefix=rds.key_prefix,
            run_id=run_id,
            conversation_id=conversation_id,
        )

        async def _stale_stream() -> AsyncIterator[bytes]:
            from datetime import UTC, datetime

            payload = {
                "type": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {
                    "error_code": "run_stale",
                    "message": "This run died before finishing.",
                },
            }
            yield _format_sse_event("0-0", payload).encode()

        return StreamingResponse(_stale_stream(), media_type="text/event-stream")
```

- [ ] **Step 9: Run route tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "bootstrap_clears_stale or stream_subscribe_emits_stale" -v`
Expected: 2 PASS.

- [ ] **Step 10: Run full conversations + streaming suites, verify no regression**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py tests/e2e/test_conversations.py tests/e2e/test_conversation_flow.py -v`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add backend/cubeplex/streams/run_events.py backend/cubeplex/api/routes/v1/conversations.py backend/tests/e2e/test_graceful_restart.py
git commit -m "feat(streams): inline stale-run detection in bootstrap and stream"
```

---

## Task 9: Drain timeout E2E (full integration)

**Files:**
- Test: `backend/tests/e2e/test_graceful_restart.py`

The unit-style drain tests in Task 4 cover the `RunManager.drain()` mechanics directly. This task adds two integration scenarios that exercise the whole pipeline end-to-end through the FastAPI test client.

- [ ] **Step 1: Write failing test for in-flight run completing during drain**

Append to `backend/tests/e2e/test_graceful_restart.py`:

```python
@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_run_then_returns(
    memory_client: httpx.AsyncClient,
) -> None:
    """Plant a slow async task in run_manager._tasks; drain should wait for it."""
    app = memory_client._transport.app  # type: ignore[attr-defined]
    rm = app.state.run_manager

    completed = asyncio.Event()

    async def slow_run() -> None:
        try:
            await asyncio.sleep(0.5)
        finally:
            completed.set()

    task = asyncio.create_task(slow_run(), name="run:integration-slow")
    rm._tasks["integration-slow"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("integration-slow"))

    start = asyncio.get_event_loop().time()
    await rm.drain(timeout_seconds=5.0)
    elapsed = asyncio.get_event_loop().time() - start
    assert completed.is_set()
    assert elapsed >= 0.4
    assert "integration-slow" not in rm._tasks


@pytest.mark.asyncio
async def test_drain_timeout_force_cancels(memory_client: httpx.AsyncClient) -> None:
    app = memory_client._transport.app  # type: ignore[attr-defined]
    rm = app.state.run_manager

    cancelled_seen = asyncio.Event()

    async def long_run() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_seen.set()
            raise

    task = asyncio.create_task(long_run(), name="run:integration-long")
    rm._tasks["integration-long"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("integration-long"))

    await rm.drain(timeout_seconds=0.2)
    assert cancelled_seen.is_set()
    assert "integration-long" not in rm._tasks
```

- [ ] **Step 2: Run tests, verify PASS**

Run: `cd backend && uv run pytest tests/e2e/test_graceful_restart.py -k "drain_waits_for_in_flight_run or drain_timeout_force" -v`
Expected: 2 PASS. (The behavior is already implemented in Task 4; these tests verify integration through the live `app.state.run_manager`.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_graceful_restart.py
git commit -m "test(graceful): integration coverage for drain through live RunManager"
```

---

## Task 10: Frontend `last_run_status` rendering

**Files:**
- Modify: `frontend/packages/core/src/api/types.ts`
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Test: `frontend/packages/core/src/stores/messageStore.test.ts` (if test file exists; otherwise add to nearest store test)

Surface stale-run failure as a tag on the most recent user message. Reducer is replay-safe — no stream subscription is opened when bootstrap returns `last_run_status: "stale"`.

- [ ] **Step 1: Add `last_run_status` to bootstrap type**

Read `frontend/packages/core/src/api/types.ts` to locate the bootstrap response type. Add `last_run_status: 'stale' | null` to the response shape. Example (adapt to actual location):

```typescript
export type ConversationBootstrap = {
  messages: ChatMessage[];
  total: number;
  active_run: ActiveRun | null;
  last_run_status: 'stale' | null;
};
```

If the existing type has no name and is inline, lift it to a named type.

- [ ] **Step 2: Carry the flag through the message store**

In `frontend/packages/core/src/stores/messageStore.ts`, locate the action that consumes a bootstrap response (search for `active_run` references). Extend the slice state with `lastRunStatus: 'stale' | null` and set it from the response. Do NOT open an SSE subscription when `last_run_status === 'stale'`.

```typescript
// inside the bootstrap reducer / action
set({
  messages: response.messages,
  activeRun: response.active_run,
  lastRunStatus: response.last_run_status,
});
```

- [ ] **Step 3: Render the stale-error bubble**

In `frontend/packages/web/components/chat/MessageList.tsx`, after the existing message render loop, add a stale notice block. Use the existing error-bubble styling for consistency with mid-stream errors:

```tsx
{lastRunStatus === 'stale' && (
  <ErrorBubble>
    上次回答未完成，请重试。
  </ErrorBubble>
)}
```

If `ErrorBubble` does not exist, copy the markup from how the `error` SSE event renders today (search for `error_code` or `error.message` in the same file).

- [ ] **Step 4: Run frontend lint + typecheck**

Run: `cd frontend && pnpm -w lint && pnpm -w typecheck`
Expected: PASS.

- [ ] **Step 5: Manual sanity check**

```
cd backend && python main.py        # terminal 1
cd frontend && pnpm dev              # terminal 2
```

Send a message; in terminal 1, kill the process with `kill -9 $(pgrep -f cubeplex)` immediately after the assistant starts streaming. Refresh the browser. The chat should show the stale error bubble beneath your last user message and unlock the input.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/api/types.ts frontend/packages/core/src/stores/messageStore.ts frontend/packages/web/components/chat/MessageList.tsx
git commit -m "feat(frontend): render stale-run error bubble from bootstrap"
```

---

## Task 11: k8s deployment example

**Files:**
- Create: `backend/docs/deploy-k8s-graceful-restart.md`

Operator-facing reference. Pairs `terminationGracePeriodSeconds` with the new probes so a rolling restart finishes draining. Per CLAUDE.md "Do not create docs without permission" — but this is a deploy-time reference for a feature the user explicitly approved, and is the only way to document the k8s pairing.

- [ ] **Step 1: Verify with user before creating**

This step is a checkpoint: confirm the user wants this doc before creating it. If they say no, mark this task complete with no file created.

- [ ] **Step 2: If approved, write the doc**

Create `backend/docs/deploy-k8s-graceful-restart.md`:

```markdown
# K8s Deployment — Graceful Restart

The cubeplex backend drains in-flight LangGraph runs on `SIGTERM` before
exiting. To get zero-downtime rolling restarts, pair this with a long
termination grace period and the split health probes.

## Probes

- `GET /health/live` → liveness. Always 200 while the process is up.
- `GET /health/ready` → readiness. 503 while draining.

## Recommended deployment fragment

```yaml
spec:
  terminationGracePeriodSeconds: 3600   # match lifecycle.graceful_drain_timeout_seconds
  containers:
    - name: cubeplex
      readinessProbe:
        httpGet: { path: /health/ready, port: 8000 }
        periodSeconds: 5
      livenessProbe:
        httpGet: { path: /health/live, port: 8000 }
        periodSeconds: 30
```

## Tunables

`backend/config.yaml`:

| Key | Default | Notes |
|---|---|---|
| `lifecycle.graceful_drain_timeout_seconds` | 3600 | Hard cap on drain wait. Match `terminationGracePeriodSeconds`. |
| `lifecycle.stale_run_threshold_seconds` | 120 | Seconds without an event before bootstrap declares a run stale. |
| `lifecycle.dev_double_signal_force_exit` | true | Second `Ctrl-C` forces immediate exit. Disable in production if needed. |

## Force-killing a slow drain

For an unscheduled exit:

```
kubectl delete pod <pod> --grace-period=0 --force
```

This skips drain. In-flight runs die mid-stream and surface to clients as
stale runs the next time the user opens the conversation.
```

- [ ] **Step 3: Commit**

```bash
git add backend/docs/deploy-k8s-graceful-restart.md
git commit -m "docs: k8s graceful-restart deployment reference"
```

---

## Self-Review Notes

- **Spec coverage:** Sections 1 (drain protocol) → Tasks 5, 7. Section 2 (implementation surface) → Tasks 3-7. Section 3 (signal handling) → Task 7. Section 4 (lifespan) → Task 7. Section 5 (RunManager.drain) → Task 4. Section 6 (DrainMiddleware) → Task 5. Section 7 (health probes) → Task 6. Section 8 (stale detection) → Tasks 1, 8. Section 9 (frontend) → Task 10. Behavior matrix entries are all covered by E2E tests in Tasks 5, 7, 8, 9. Testing strategy section (E2E #1-9 + unit) → Tasks 3, 4, 5, 6, 7, 8, 9.
- **Type consistency:** `RunMeta.last_event_at` is consistently `str | None`. `is_stale_meta` accepts `RunMeta` directly (not raw hash). `mark_run_stale` arguments mirror `create_run`. `DrainState` exposes `is_accepting`, `is_draining`, `enter_draining` — used identically in middleware, health, and lifespan.
- **Frequent commits:** every task ends with a commit. Total: 10 commits across 11 tasks (Task 11 only commits if user approves the doc).
- **TDD:** tests precede implementation in every code-touching task.
