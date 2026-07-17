# Graceful Restart Design

## Problem

When the FastAPI process restarts, every in-flight agent run dies. The
LangGraph execution is owned by an in-process `asyncio.Task` inside the API
server, so `SIGTERM`, `uvicorn` reload, and pod replacement all interrupt the
user's current answer mid-stream.

The existing `resumable-run-streaming` design (2026-04-23) already decouples
the browser connection from the run via a Redis event log. That solves
**page refresh** during a run, but it does not protect the run from a
**process restart** — once the worker `asyncio.Task` is cancelled, no further
events are appended to Redis and the user is left with a half-finished
response.

This design introduces a process-level drain protocol so that on planned
restart the server stops accepting new runs, waits for in-flight runs to
finish naturally, and only then exits. It also adds stale-run detection to
recover from non-graceful death (SIGKILL, OOM, host loss).

## Goal

- On `SIGTERM` / `SIGINT` / lifespan shutdown: stop accepting new runs and
  wait until existing runs complete before exiting.
- On k8s rolling restart: zero observable interruption for clients whose run
  is already in flight, given a sufficiently long
  `terminationGracePeriodSeconds`.
- On non-graceful death: the next client interaction recovers — surface a
  clear "previous run failed" signal instead of leaving a phantom in-flight
  run forever.

## Non-Goals

- Cross-process run handoff. A run that started on pod A finishes on pod A
  or fails on pod A. We do not transfer the LangGraph execution to pod B.
- Resuming a run from the last LangGraph checkpoint. A killed run is a
  failed run; the user re-issues the message.
- Multi-instance scheduling fairness or queueing. Drain is per-pod; the
  load balancer is responsible for steering new traffic away from a draining
  pod via the readiness probe.

## Solution

A `DrainState` singleton on `app.state` gates write paths. Signal handlers
flip the state to `draining`, the API begins refusing new run starts with
`503`, the readiness probe begins reporting `503`, and `RunManager.drain()`
waits for the in-flight task set to empty (or hits a long timeout, default
3600s, after which it cancels remaining tasks via the existing cancel path).

For non-graceful death, a wall-clock `last_event_at` field is added to run
metadata and stamped on every event append. A bootstrap or stream-subscribe
that observes `status=running` with `last_event_at` older than 120s treats
the run as `stale`, atomically clears the active-run lock, and surfaces the
failure to the frontend.

## Architecture

### 1. Drain state machine

```
ACCEPTING ──signal──▶ DRAINING ──tasks_empty─▶ EXITING
                          │
                          ├──timeout──▶ EXITING (cancel_all)
                          └──2nd signal──▶ EXITING (force, dev)
```

`DrainState` is a small object with:

- `_state: Literal["accepting", "draining", "exiting"]`
- `is_draining() -> bool`
- `enter_draining() -> None` (idempotent)

The state is owned by the FastAPI app (`app.state.drain_state`) and read by
the drain middleware and health routes.

### 2. Signal handling

Signal handlers are registered inside the lifespan startup (before `yield`)
on the running asyncio loop:

```python
loop = asyncio.get_running_loop()
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, _on_signal, sig.name)
```

`loop.add_signal_handler` runs the callback inside the loop, avoiding the
cross-thread synchronization issues of `signal.signal`.

Behavior:

- First signal: log, call `state.enter_draining()`, return.
- Second signal during drain: if
  `lifecycle.dev_double_signal_force_exit` is true, schedule
  `run_manager.cancel_all()` and raise `SystemExit(130)`. This is the dev
  convenience path so a developer is not held by a 60-minute timeout.

### 3. Lifespan shutdown

After `yield`, lifespan unconditionally calls
`state.enter_draining()` (covering uvicorn reload and other paths that do
not deliver a signal), then awaits
`run_manager.drain(timeout=lifecycle.graceful_drain_timeout_seconds)` before
closing Redis and other resources.

### 4. RunManager changes

- The existing `RunManager.shutdown()` is renamed to `cancel_all()` to
  reflect its semantics (cancel every in-flight task, no waiting). It
  remains the implementation of forced cancellation.
- A new `RunManager.drain(timeout_seconds: float) -> None`:
  - Maintains an `asyncio.Event` set whenever `_tasks` becomes empty (set
    from the existing `task.add_done_callback`).
  - `await asyncio.wait_for(self._tasks_empty.wait(), timeout)`.
  - On `TimeoutError`: log a warning with the residual count, await
    `cancel_all()`, return. The existing per-task cancel path already
    writes an `error` event, marks `status=cancelled`, and releases the
    `conversation_active_run` lock.
- A periodic logger inside `drain()` emits `still draining: N runs
  remaining` every 30 seconds for operator visibility.

### 5. Drain middleware

A new ASGI middleware `DrainMiddleware`:

- For request paths matching the run-start surface (initially:
  `POST /api/v1/ws/{ws}/conversations/{cid}/messages`), if
  `state.is_draining()`, return:

  ```
  503 Service Unavailable
  Retry-After: 5
  Connection: close
  Content-Type: application/json
  {"error": {"code": "draining", "message": "Server is draining; retry."}}
  ```

- All other paths pass through unchanged. SSE subscription
  (`GET /runs/{run_id}/stream`), bootstrap, auth, health, etc. continue to
  work during drain.

`DrainMiddleware` is registered last in `create_app`, which makes it the
outermost wrapper on the request path. A draining server should refuse new
runs before doing CSRF or identity work.

### 6. Health probes

The current single `/health` endpoint is replaced by:

- `GET /health/live` — always `200` while the process is up. Used by k8s
  liveness probe; should not flip during drain or k8s will kill the pod
  before drain completes.
- `GET /health/ready` — `200` when `state.is_accepting()`, `503` when
  draining. Used by k8s readiness probe so the load balancer stops sending
  new traffic during drain.

The legacy `/health` is removed, not aliased.

### 7. Stale run detection

Required because non-graceful death (SIGKILL, OOM, host loss) leaves
`active_run` pointing at a `running` run whose worker is dead.

Schema change in `RunMeta`:

- New field `last_event_at: str` (ISO-8601 UTC).
- Stamped atomically inside `_APPEND_EVENT_LUA` on every event append. The
  Lua script is extended to take `last_event_at` as an additional argv and
  set it via `HSET`.

Detection logic — pure helper, no background scanner:

```python
def is_stale(meta: RunMeta, now: datetime, threshold_s: int) -> bool:
    return (
        meta.status == "running"
        and (now - parse(meta.last_event_at)).total_seconds() > threshold_s
    )
```

Recovery is performed inline at two read sites:

1. `GET /conversations/{id}/bootstrap`: if the active run is stale, run a
   Lua CAS that
   - `HSET run_meta status=stale` only if it is still `running`,
   - `DEL active_run` only if it still points to that `run_id`,
   then return `active_run: null` and `last_run_status: "stale"`.
2. `GET /runs/{run_id}/stream`: same CAS, then immediately emit a synthetic
   SSE event `{"type": "error", "data": {"error_code": "run_stale", ...}}`
   and close the stream.

The CAS is idempotent: concurrent bootstraps observe the cleared state on
their second read.

### 8. Frontend

- `bootstrap` response gains `last_run_status: "stale" | null`. `"stale"`
  is set only when this bootstrap call detected and cleared a stale run.
  Healthy in-flight runs are signaled the existing way via the `active_run`
  field; runs that completed normally produce `null`. The field exists only
  to surface an inline-resolved failure to the user — it is not an audit
  log of past run outcomes.
- When `last_run_status === "stale"`, the frontend renders an error bubble
  beneath the most recent user message ("上次回答未完成，请重试") and unlocks
  the input. No active SSE subscription is opened.
- An in-flight SSE subscription that receives an event with
  `error_code: "run_stale"` flows through the existing error rendering
  path; no special-case code is needed in the reducer.

## Data Model

### `RunMeta` (Redis hash, additive change)

```
run_id           string
conversation_id  string
status           string  # running | completed | cancelled | failed | stale
started_at       string  # ISO-8601
user_message     string
first_event_id   string
last_event_id    string
last_event_at    string  # NEW. ISO-8601, stamped on every append.
```

The status value `stale` is new; the rest are unchanged.

### Lua script changes

`_APPEND_EVENT_LUA` gains a `last_event_at` argv:

```
KEYS[1]=stream  KEYS[2]=meta  KEYS[3]=active
ARGV[1]=payload_json  ARGV[2]=ttl  ARGV[3]=maxlen
ARGV[4]=run_id        ARGV[5]=last_event_at_iso
```

`HSET` receives `last_event_at` alongside `last_event_id`.

A new `_MARK_STALE_LUA`:

```
KEYS[1]=meta_key  KEYS[2]=active_key
ARGV[1]=expected_run_id
if redis.call('HGET', KEYS[1], 'status') == 'running' then
  redis.call('HSET', KEYS[1], 'status', 'stale')
end
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[2])
end
return 1
```

## Configuration

Added to `config.yaml`:

```yaml
lifecycle:
  graceful_drain_timeout_seconds: 3600    # 60 min
  dev_double_signal_force_exit: true
  stale_run_threshold_seconds: 120
```

Recommended k8s deployment:

```yaml
spec:
  terminationGracePeriodSeconds: 3600
  containers:
    - readinessProbe:
        httpGet: { path: /health/ready, port: 8000 }
        periodSeconds: 5
      livenessProbe:
        httpGet: { path: /health/live, port: 8000 }
        periodSeconds: 30
```

## Files Affected

New:

- `backend/cubeplex/lifecycle/__init__.py`
- `backend/cubeplex/lifecycle/drain.py` — `DrainState`
- `backend/cubeplex/api/middleware/drain.py` — `DrainMiddleware`
- `backend/tests/e2e/test_graceful_restart.py`
- `backend/tests/unit/test_drain_state.py`

Modified:

- `backend/cubeplex/api/app.py` — signal registration, lifespan shutdown
  ordering, middleware wiring
- `backend/cubeplex/api/routes/health.py` — split into `/health/live` and
  `/health/ready`, drop `/health`
- `backend/cubeplex/api/routes/v1/conversations.py` — bootstrap stale
  detection + `last_run_status` field; stream endpoint stale detection
- `backend/cubeplex/streams/run_manager.py` — rename `shutdown()` →
  `cancel_all()`, add `drain()`, maintain `_tasks_empty` event, periodic
  drain progress logger, stamp `last_event_at` on appends
- `backend/cubeplex/streams/run_events.py` — add `last_event_at` to
  `RunMeta`, extend `_APPEND_EVENT_LUA`, add `_MARK_STALE_LUA`, add
  `mark_run_stale()` helper
- `backend/config.yaml`, `backend/config.development.yaml`,
  `backend/config.production.yaml` — `lifecycle.*` keys
- `frontend/packages/core/src/api/types.ts` — `last_run_status` on
  bootstrap
- `frontend/packages/core/src/stores/messageStore.ts` — render stale-run
  error bubble
- `frontend/packages/web/components/chat/MessageList.tsx` — wire the new
  rendering

## Behavior Matrix

| Scenario | Outcome |
|---|---|
| Drain begins, run finishes naturally | Normal completion: `done` event, `active_run` cleared, task removed from `_tasks` |
| Drain timeout exceeded | `cancel_all()` runs; per-task cancel path writes `error`, sets `status=cancelled`, releases lock |
| New `POST /messages` during drain | `503` + `Retry-After: 5` |
| Existing SSE subscription during drain | Continues; reads from Redis |
| New SSE subscription during drain | Accepted; not gated by drain |
| Same-conversation `POST /messages` while old run still drains | `503` (lock held); resolves once old run finishes |
| `SIGKILL` / OOM (no drain) | Worker dies; `active_run` orphaned; next bootstrap or stream subscribe detects stale and clears |
| Bootstrap detects stale | Returns `active_run: null`, `last_run_status: "stale"`; frontend shows error bubble |
| Stream subscribe detects stale | Emits synthetic `error` event with `error_code: "run_stale"`, closes |
| Two bootstraps race on the same stale run | CAS makes the second observe cleared state |
| `uvicorn` reload (no signal) | Lifespan shutdown still runs `drain()` |
| Second `Ctrl-C` in dev | `cancel_all()` + `SystemExit(130)` |

## Testing Strategy

E2E (real Redis, `tests/e2e/test_graceful_restart.py`):

1. Drain rejects new `POST /messages` with `503` + `Retry-After`.
2. Existing SSE subscription continues to receive events through drain.
3. Drain returns immediately after the in-flight run finishes naturally
   (run < timeout).
4. Drain hits timeout, `cancel_all()` runs, `status=cancelled`, last event
   is `error`.
5. `/health/ready` returns `503` during drain; `/health/live` stays `200`.
6. After drain, on a fresh app instance, the same conversation accepts new
   `POST /messages` (lock released).
7. Stale detection via bootstrap: planted `active_run` with old
   `last_event_at` → bootstrap returns `last_run_status: "stale"` and
   active-run key is removed.
8. Stale detection via stream subscribe: same setup → SSE delivers a
   synthetic `error_code: run_stale` event and closes.
9. Stale detection idempotency: two concurrent bootstraps both observe
   cleared state without crashing.

Unit (`tests/unit/test_drain_state.py`):

- `DrainState` transitions and idempotency.
- `RunManager.drain()` against a stub task set: empty set returns
  immediately; never-completing task triggers timeout path; running
  task that completes mid-drain returns cleanly.

Manual verification (documented, not automated):

- Start `python main.py`, send a message, `Ctrl-C` once → see drain log
  and run finish before exit.
- `Ctrl-C` twice → immediate force exit.
- k8s rollout restart with `terminationGracePeriodSeconds: 3600` and the
  readiness probe wired to `/health/ready` → load balancer routes new
  traffic to fresh pod, in-flight runs on draining pod finish.

Signal handling itself is exercised manually rather than via pytest because
`signal.signal` registration interacts poorly with pytest's threading.
Code paths after `state.enter_draining()` are fully covered by directly
calling that method in tests.

## Why This Design

- **No new infrastructure.** No worker pool, no message queue, no second
  service. The existing in-process model is preserved; only its lifecycle
  becomes well-defined.
- **Forward-compatible with multi-instance.** A k8s rolling restart with
  per-pod drain plus the readiness probe yields zero-downtime deploys
  without any cross-pod coordination, because the Redis event log already
  serves as the cross-process recovery substrate.
- **Stale detection patches a pre-existing bug.** Even before this design,
  a SIGKILL would orphan `active_run` keys; the inline detection costs
  one Redis hash field and one Lua script and removes that footgun.

## Tradeoffs

- A pod that takes 60 minutes to drain blocks deploys for 60 minutes. This
  is acceptable per product input ("一般上线时间长可以接受, 有特殊情况可以
  强杀 pod"); operators can `kubectl delete pod --grace-period=0` for an
  immediate exit at the cost of failing the longest-running runs.
- Stale runs surface only on user interaction, not proactively. A
  conversation that no one opens after a SIGKILL stays in `active_run` until
  the 12h TTL expires. This is the intended trade for not running a
  background scanner.
- Drain refuses new starts on the draining pod, which during a single-pod
  deployment briefly fails new `POST /messages` calls. Multi-pod deployments
  hide this behind the load balancer; single-pod operators accept the brief
  unavailability window.
