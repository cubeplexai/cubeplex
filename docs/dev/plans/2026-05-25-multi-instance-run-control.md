# Multi-Instance Run Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `cancel` and `steer` work across backend instances: a control request landing on any instance reaches the instance running the agent, via a Redis pub/sub control channel + local fast-path, with a bounded ack for cross-instance cancel and a unified `{status, run_id}` response.

**Architecture:** See `docs/dev/specs/2026-05-25-multi-instance-run-control-design.md`. Summary: one shared channel `{key_prefix}:control` (every instance subscribes once, filters by owning `run_id`); one shared ack channel `{key_prefix}:control:ack` + per-instance `run_id → list[Future]` map; endpoints take a local fast-path or publish; cross-instance cancel waits for the owner's post-cleanup ack (`cancelled`) or times out (`published`).

**Tech Stack:** FastAPI, redis.asyncio pub/sub, cubepi runtime, Zustand/React, pytest + fakeredis + vitest.

**Non-goals (from spec):** changing the SSE transport; sticky LB routing; strong consumption guarantees for cross-instance steer.

---

## File Structure

- Modify `backend/cubeplex/streams/run_manager.py` — control/ack channels, `_ack_waiters`, publish helpers, two supervised pub/sub listeners (reconnecting), `_handle_control`, `dispatch_steer`/`dispatch_cancel`, `start_control_listeners`/`stop_control_listeners`.
- Modify `backend/cubeplex/api/app.py` — start listeners after `RunManager` creation; stop them on shutdown after `drain`.
- Modify `backend/cubeplex/api/routes/v1/conversations.py` — `cancel_active_run` + `steer_active_run` return unified `{status, run_id}` via `dispatch_*`.
- Modify `frontend/packages/core/src/api/stream.ts` — `CancelRunResponse`/`SteerRunResponse` → `{status, run_id}`.
- Modify `frontend/packages/core/src/stores/messageStore.ts` — `steer` rollback rule (only `no_active_run`); `cancelStream` records resend-safety from `status`; `send` retries once on 409.
- Tests: `backend/tests/unit/test_run_control_pubsub.py` (dispatch + listeners + ack + reconnect, fakeredis), `backend/tests/unit/test_run_control_crossinstance.py` (two `RunManager`s, one Redis), frontend `messageStoreSteer`/`messageStoreCancel` updates.

---

## Task 1: RunManager — control + ack pub/sub infrastructure

**Files:** Modify `backend/cubeplex/streams/run_manager.py`; Test `backend/tests/unit/test_run_control_pubsub.py`.

- [ ] **Step 1: Failing tests**

Create `backend/tests/unit/test_run_control_pubsub.py`. Uses `fakeredis.aioredis` (dev dep). A fake agent/task stand in for the live handles.

```python
import asyncio
import json

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_manager import RunManager


def _mgr(redis) -> RunManager:
    m = RunManager.__new__(RunManager)  # type: ignore[call-arg]
    m._redis = redis
    m._key_prefix = "t"
    m._tasks = {}
    m._agents = {}
    m._ack_waiters = {}
    m._control_stopping = False
    return m


class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list[str] = []

    def steer(self, message) -> None:  # noqa: ANN001
        self.steered.append(message.content[0].text)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.mark.asyncio
async def test_dispatch_steer_local_calls_agent(redis):
    m = _mgr(redis)
    agent = _FakeAgent()
    m._agents["r1"] = agent
    status = await m.dispatch_steer("r1", "go left")
    assert status == "steered"
    assert agent.steered == ["go left"]


@pytest.mark.asyncio
async def test_dispatch_steer_remote_publishes(redis):
    m = _mgr(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"t:control")
    await asyncio.sleep(0)  # let subscribe settle
    status = await m.dispatch_steer("r-remote", "hello")
    assert status == "published"
    # a control message was published
    got = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg:
            got = json.loads(msg["data"])
            break
    assert got == {"run_id": "r-remote", "type": "steer", "content": "hello"}


@pytest.mark.asyncio
async def test_handle_control_steer_dispatches_locally(redis):
    m = _mgr(redis)
    agent = _FakeAgent()
    m._agents["r1"] = agent
    await m._handle_control({"run_id": "r1", "type": "steer", "content": "x"})
    assert agent.steered == ["x"]


@pytest.mark.asyncio
async def test_handle_control_unknown_run_is_ignored(redis):
    m = _mgr(redis)
    # no handle registered → no error, no action
    await m._handle_control({"run_id": "ghost", "type": "steer", "content": "x"})
    await m._handle_control({"run_id": "ghost", "type": "cancel"})


@pytest.mark.asyncio
async def test_cancel_ack_resolves_waiter(redis):
    m = _mgr(redis)
    fut = asyncio.get_running_loop().create_future()
    m._ack_waiters["r1"] = [fut]
    await m._handle_ack({"run_id": "r1"})
    assert fut.done() and fut.result() is True


@pytest.mark.asyncio
async def test_dispatch_cancel_remote_times_out_to_published(redis):
    m = _mgr(redis)
    # no owner will ack → wait_for times out → "published"
    status = await m.dispatch_cancel("r-remote", ack_timeout=0.2)
    assert status == "published"
    assert m._ack_waiters.get("r-remote") in (None, [])
```

Run: `cd backend && uv run pytest tests/unit/test_run_control_pubsub.py -v` → FAIL (methods/attrs missing).

- [ ] **Step 2: Add fields + channel names in `__init__`**

In `RunManager.__init__`, after `self._agents: dict[str, Any] = {}`:

```python
        self._ack_waiters: dict[str, list[asyncio.Future[bool]]] = {}
        self._control_channel = f"{key_prefix}:control"
        self._ack_channel = f"{key_prefix}:control:ack"
        self._control_stopping = False
        self._control_tasks: list[asyncio.Task[None]] = []
```

(`key_prefix` is the existing `__init__` param.)

- [ ] **Step 3: Publish helpers + dispatch methods**

Add to `RunManager` (near `cancel_run`/`steer_run`):

```python
    async def _publish_control(self, run_id: str, type_: str, content: str | None = None) -> None:
        import json

        payload: dict[str, Any] = {"run_id": run_id, "type": type_}
        if content is not None:
            payload["content"] = content
        await self._redis.publish(self._control_channel, json.dumps(payload))

    async def _publish_ack(self, run_id: str) -> None:
        import json

        await self._redis.publish(self._ack_channel, json.dumps({"run_id": run_id}))

    async def dispatch_steer(self, run_id: str, content: str) -> str:
        """Steer locally if the agent is here, else broadcast. Returns status."""
        agent = self._agents.get(run_id)
        if agent is not None:
            from cubepi.providers.base import TextContent, UserMessage

            agent.steer(UserMessage(content=[TextContent(text=content)]))
            return "steered"
        await self._publish_control(run_id, "steer", content)
        return "published"

    async def dispatch_cancel(self, run_id: str, ack_timeout: float = 3.0) -> str:
        """Cancel locally if the task is here, else broadcast + await the owner's
        post-cleanup ack. Returns "cancelled" or (on timeout) "published"."""
        if run_id in self._tasks:
            await self.cancel_run(run_id)
            return "cancelled"

        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._ack_waiters.setdefault(run_id, []).append(fut)
        try:
            await self._publish_control(run_id, "cancel")
            await asyncio.wait_for(fut, timeout=ack_timeout)
            return "cancelled"
        except TimeoutError:
            return "published"
        finally:
            waiters = self._ack_waiters.get(run_id)
            if waiters and fut in waiters:
                waiters.remove(fut)
                if not waiters:
                    self._ack_waiters.pop(run_id, None)
```

- [ ] **Step 4: Dispatch handlers**

```python
    async def _handle_control(self, data: dict[str, Any]) -> None:
        run_id = data.get("run_id")
        type_ = data.get("type")
        if not isinstance(run_id, str):
            return
        if type_ == "cancel":
            if run_id in self._tasks:
                await self.cancel_run(run_id)
                # Tell the requesting instance cleanup is done (active-run key cleared).
                await self._publish_ack(run_id)
        elif type_ == "steer":
            agent = self._agents.get(run_id)
            if agent is not None:
                from cubepi.providers.base import TextContent, UserMessage

                content = data.get("content") or ""
                agent.steer(UserMessage(content=[TextContent(text=content)]))

    async def _handle_ack(self, data: dict[str, Any]) -> None:
        run_id = data.get("run_id")
        if not isinstance(run_id, str):
            return
        for fut in self._ack_waiters.get(run_id, []):
            if not fut.done():
                fut.set_result(True)
```

- [ ] **Step 5: Supervised reconnecting listeners + lifecycle**

```python
    async def _subscribe_loop(self, channel: str, handler: Any, ready: asyncio.Event) -> None:
        import json

        backoff = 0.5
        while not self._control_stopping:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                ready.set()  # signal first (and every) successful subscribe
                backoff = 0.5
                async for msg in pubsub.listen():
                    if self._control_stopping:
                        break
                    if msg.get("type") != "message":
                        continue
                    # Per-message containment: a bad payload or handler fault must
                    # never break the read loop and deafen the instance.
                    try:
                        await handler(json.loads(msg["data"]))
                    except Exception:
                        logger.warning("control handler error on {}", channel, exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("control listener {} dropped; reconnecting", channel, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                with suppress(Exception):
                    await pubsub.aclose()

    async def start_control_listeners(self, ready_timeout: float = 5.0) -> None:
        self._control_stopping = False
        ctrl_ready = asyncio.Event()
        ack_ready = asyncio.Event()
        self._control_tasks = [
            asyncio.create_task(
                self._subscribe_loop(self._control_channel, self._handle_control, ctrl_ready),
                name="run-control-listener",
            ),
            asyncio.create_task(
                self._subscribe_loop(self._ack_channel, self._handle_ack, ack_ready),
                name="run-control-ack-listener",
            ),
        ]
        # Don't return until both channels are actually subscribed, so controls
        # published right after startup aren't lost. Bounded so a Redis hiccup
        # can't hang boot.
        with suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(ctrl_ready.wait(), ack_ready.wait()), timeout=ready_timeout
            )

    async def stop_control_listeners(self) -> None:
        self._control_stopping = True
        for t in self._control_tasks:
            t.cancel()
        for t in self._control_tasks:
            with suppress(asyncio.CancelledError):
                await t
        self._control_tasks = []
```

(`logger` and `suppress` are already imported in this module; `asyncio` too.)

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_run_control_pubsub.py -v && uv run mypy cubeplex/streams/run_manager.py`
Expected: all pass; mypy Success. (The `test_dispatch_cancel_remote_times_out_to_published` test exercises the timeout→published path; `_handle_ack` test the resolve path.)

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/unit/test_run_control_pubsub.py
git commit -m "feat(runs): Redis pub/sub control + ack infra on RunManager"
```

---

## Task 2: Wire listeners to the app lifespan

**Files:** Modify `backend/cubeplex/api/app.py`.

- [ ] **Step 1: Start listeners after RunManager is created**

In `create_app`'s lifespan, find where `run_manager = RunManager(...)` is built and stashed on `_app.state.run_manager` (~line 168-175). Right after stashing it, add:

```python
        await run_manager.start_control_listeners()
```

- [ ] **Step 2: Stop listeners on shutdown, after drain**

Find the shutdown path where `await run_manager.drain(timeout_seconds=...)` runs (~line 311-316). **After** the drain call, add:

```python
        await run_manager.stop_control_listeners()
```

(Order matters: drain first so in-flight runs can still receive controls during graceful shutdown, then stop the listeners.)

- [ ] **Step 3: Sanity-check the app boots**

Run: `cd backend && uv run python -c "from cubeplex.api.app import create_app; create_app(); print('ok')"`
Expected: `ok` (or, if `create_app` needs runtime env, grep-verify both calls are present and report).

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/app.py
git commit -m "feat(runs): start/stop run-control listeners with app lifespan"
```

---

## Task 3: Endpoints — unified `{status, run_id}` via dispatch

**Files:** Modify `backend/cubeplex/api/routes/v1/conversations.py`.

- [ ] **Step 1: Rewrite `steer_active_run` body**

Keep the empty-content guard, conversation lookup, and active-run lookup. Replace the tail (`run_manager.steer_run(...)`) with:

```python
    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    run_manager = raw_request.app.state.run_manager
    status = await run_manager.dispatch_steer(active_run.run_id, body.content)
    return {"status": status, "run_id": active_run.run_id}
```

- [ ] **Step 2: Rewrite `cancel_active_run` body**

Replace the `run_manager.cancel_run(...)` tail with:

```python
    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    run_manager = raw_request.app.state.run_manager
    status = await run_manager.dispatch_cancel(active_run.run_id)
    return {"status": status, "run_id": active_run.run_id}
```

- [ ] **Step 3: Lint + typecheck + existing E2E (local fast-path unchanged)**

```
cd backend && uv run ruff check cubeplex/api/routes/v1/conversations.py && uv run mypy cubeplex/api/routes/v1/conversations.py
```
The existing `tests/e2e/test_steer_endpoint.py` still asserts `s.json()["steered"]` — update it to `s.json()["status"] == "steered"` (single instance → local fast-path → `steered`).

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/routes/v1/conversations.py backend/tests/e2e/test_steer_endpoint.py
git commit -m "feat(api): unified {status,run_id} for cancel/steer via dispatch"
```

---

## Task 4: Frontend — `status` responses + resend safety

**Files:** Modify `frontend/packages/core/src/api/stream.ts`, `frontend/packages/core/src/stores/messageStore.ts`; update `__tests__/stores/messageStoreSteer.test.ts` + `messageStoreCancel.test.ts`.

- [ ] **Step 1: Response types**

In `stream.ts`:

```typescript
export interface CancelRunResponse {
  status: 'cancelled' | 'published' | 'no_active_run'
  run_id: string | null
}
export interface SteerRunResponse {
  status: 'steered' | 'published' | 'no_active_run'
  run_id: string | null
}
```

- [ ] **Step 2: `steer` rollback rule**

In `messageStore.ts` `steer`, change the rollback condition from `if (!res.steered) rollback()` to:

```typescript
      const res = await steerRun(client, conversationId, text)
      if (res.status === 'no_active_run') rollback()
```

(Keep the bubble for `steered` and `published`.)

- [ ] **Step 3: `send` retries once on 409 (resend-after-cancel safety net)**

In `send`, the run-start request can 409 if a just-cancelled run is still releasing its active-run lock (cross-instance cancel returned `published`, or a fast resend). Wrap the initial start so a single 409 retries after a short delay. Concretely, in `streamMessages` consumption: when the first event is `error` with an HTTP 409 message, retry the POST once after ~400ms before surfacing the error. (Implement minimally: catch the 409 path in `send` and re-invoke the stream once; if it 409s again, surface the error as today.)

```typescript
// in send(), around the streamMessages loop — pseudo-anchor; match existing code:
// if the stream's first yielded event is an HTTP 409 error, await 400ms and retry the
// stream once; otherwise proceed. Only one retry, then fall through to existing error handling.
```

- [ ] **Step 4: Update tests**

`messageStoreSteer.test.ts`: the mock returns `{ status: 'steered', run_id: 'r1' }`; the rollback test mocks `{ status: 'no_active_run', run_id: null }`; add a `{ status: 'published' }` case asserting the bubble is **kept**.
`messageStoreCancel.test.ts`: `cancelActiveRun` mock returns `{ status: 'cancelled', run_id: 'r1' }`.

Run: `cd frontend && pnpm --filter @cubeplex/core test && pnpm --filter @cubeplex/core build`
Expected: green; `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/stream.ts frontend/packages/core/src/stores/messageStore.ts frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts frontend/packages/core/__tests__/stores/messageStoreCancel.test.ts
git commit -m "feat(store): status-based control responses + 409 resend retry"
```

---

## Task 5: Cross-instance integration test (two RunManagers, one Redis)

**Files:** Test `backend/tests/unit/test_run_control_crossinstance.py`.

- [ ] **Step 1: Write the test**

Two `RunManager`s share one `fakeredis` client. A (owner) registers a handle + starts listeners; B's `dispatch_*` publishes; assert A acts.

```python
import asyncio

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_manager import RunManager


def _mgr(redis):
    m = RunManager.__new__(RunManager)  # type: ignore[call-arg]
    m._redis = redis
    m._key_prefix = "t"
    m._tasks = {}
    m._agents = {}
    m._ack_waiters = {}
    m._control_channel = "t:control"
    m._ack_channel = "t:control:ack"
    m._control_stopping = False
    m._control_tasks = []
    return m


class _FakeAgent:
    def __init__(self):
        self.steered = []

    def steer(self, message):
        self.steered.append(message.content[0].text)


@pytest.mark.asyncio
async def test_cross_instance_steer():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    a, b = _mgr(redis), _mgr(redis)
    agent = _FakeAgent()
    a._agents["r1"] = agent  # owner is A
    await a.start_control_listeners()
    try:
        status = await b.dispatch_steer("r1", "redirect")  # request lands on B
        assert status == "published"
        for _ in range(50):
            if agent.steered:
                break
            await asyncio.sleep(0.05)
        assert agent.steered == ["redirect"]
    finally:
        await a.stop_control_listeners()


@pytest.mark.asyncio
async def test_cross_instance_cancel_ack():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    a, b = _mgr(redis), _mgr(redis)

    cancelled = asyncio.Event()

    async def fake_cancel(run_id):
        cancelled.set()
        return True

    a._tasks["r1"] = object()  # owner A "has" the task
    a.cancel_run = fake_cancel  # type: ignore[assignment]
    await a.start_control_listeners()
    await b.start_control_listeners()  # B needs its ack listener to resolve its future
    try:
        status = await b.dispatch_cancel("r1", ack_timeout=3.0)
        assert status == "cancelled"
        assert cancelled.is_set()
    finally:
        await a.stop_control_listeners()
        await b.stop_control_listeners()
```

Run: `cd backend && uv run pytest tests/unit/test_run_control_crossinstance.py -v`
Expected: both pass (A receives B's steer; B's cancel resolves via A's ack).

- [ ] **Step 2: Commit**

```bash
git add backend/tests/unit/test_run_control_crossinstance.py
git commit -m "test(runs): cross-instance steer + cancel-ack integration (shared fakeredis)"
```

---

## Task 6: Verification sweep

**Files:** none.

- [ ] **Step 1: Worktree E2E config** (copy if absent — see prior plan):
`cp /home/chris/cubeplex/backend/.env backend/.env; cp /home/chris/cubeplex/backend/config.development.local.yaml backend/config.development.local.yaml` (the worktree script usually copies these; skip if present). Migrate the worktree test DB if needed: `CUBEPLEX_DATABASE__NAME=cubeplex_test_feat_run_control_pubsub ENV_FOR_DYNACONF=test uv run alembic upgrade head`.

- [ ] **Step 2: Backend changed-module tests**
`cd backend && uv run pytest tests/unit/test_run_control_pubsub.py tests/unit/test_run_control_crossinstance.py -v`

- [ ] **Step 3: real-LLM E2E still green via local fast-path**
`cd backend && uv run pytest tests/e2e/test_steer_endpoint.py -v` (single instance → `status == "steered"`).

- [ ] **Step 4: Frontend tests**
`cd frontend && pnpm --filter @cubeplex/core test`

- [ ] **Step 5: Full sweep**
`cd /home/chris/cubeplex/.worktrees/feat/run-control-pubsub && make check-ci`

- [ ] **Step 6:** `/finishing-a-development-branch` → PR → `/pr-codex-review-loop`.

---

## Self-Review notes

- **Owner publishes ack only via `_handle_control`** (cross-instance cancels). Local fast-path cancels don't publish (no remote waiter) — confirmed: `dispatch_cancel`'s local branch calls `cancel_run` directly and returns `cancelled`.
- **Ack waiter cleanup** happens in `dispatch_cancel`'s `finally` (both resolved + timeout paths) — no leak.
- **At-most-once apply:** the publishing instance also receives its own broadcast but `run_id` isn't in its `_tasks`/`_agents` (that's why it published) → filtered. Owner applies once.
- **Reconnect:** `_subscribe_loop` recreates the pubsub + re-subscribes on any non-cancel exception with capped backoff; `_control_stopping` + task cancel stop it cleanly.
- **Readiness window / owner-crash / 409 race:** documented in the spec as accepted behavior; the `send` 409-retry (Task 4 Step 3) is the client mitigation for the resend race; cross-instance cancel's ack (`cancelled`) is the primary fix.
- **Type consistency:** `dispatch_steer`→`{steered|published}`, `dispatch_cancel`→`{cancelled|published}`, endpoints add `no_active_run`; frontend `status` unions match exactly.
