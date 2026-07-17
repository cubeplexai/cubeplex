# Multi-Instance Run Control (cancel + steer) — Design

**Date:** 2026-05-25
**Status:** Draft for review
**Author:** (agent-assisted)

## Problem

`cancel` and `steer` are *control signals* aimed at an in-flight run. Today both
find the run's live handle in a **process-local dict** on `RunManager`:

- `cancel_run(run_id)` → `self._tasks[run_id]` (the background `asyncio.Task`).
- `steer_run(run_id, content)` → `self._agents[run_id]` (the live cubepi `Agent`).

A run executes as a background task **in the single uvicorn process that
received `POST .../messages`**. With more than one backend instance behind a
load balancer, a `cancel`/`steer` request can land on a *different* instance,
whose `RunManager` has no handle for that `run_id`. Result:

- cross-instance **cancel** → silent no-op (the run keeps going).
- cross-instance **steer** → endpoint returns `steered: false` (message dropped).

This blocks horizontal scaling of the backend. `cancel` already has this defect
today; `steer` (shipped in #132) inherits it.

## What is already multi-instance-safe (do NOT change)

- **Run state** lives in Redis: active-run key, run-events Stream
  (`cubeplex/streams/run_events.py` — `create_run`, `get_active_run`,
  `append_run_event`, `read_run_events_after`).
- **SSE read path** (`_build_run_streaming_response` in
  `api/routes/v1/conversations.py`) replays + tails the Redis Stream, so **any**
  instance can serve the live stream for a run executing on another instance.
- **History / reload** reads the shared Postgres checkpointer (`cubepi_messages`).
- **Steered-message display** is a client-side optimistic append + reload from
  the shared checkpointer — instance-independent.

So the run **read** path is already cross-instance. Only the **control** path
is not.

### Rationale: why SSE keeps tailing the Redis Stream (not "read checkpoint directly")

The Redis run-events Stream and the checkpointer hold different things at
different granularity, and are **complementary, not redundant**:

| | Redis run-events Stream | Postgres checkpointer |
|---|---|---|
| granularity | incremental events (text_delta, tool_call, tool_result, reasoning, usage) — token-level | complete messages, appended on cubepi `MessageEnd` |
| timing | during generation, live | only after a message finishes |
| purpose | live streaming + reconnect replay + cross-instance fan-out | durable post-completion history |

cubepi persists a whole message at `MessageEnd`; it stores **no sub-message
deltas**. Reading the checkpointer "directly" for the live stream would mean a
response only appears once each message is fully complete — no token streaming,
no live tool/reasoning progress — a clear UX regression. It would also force the
SSE connection onto the owning instance (sticky reads), which is exactly what
the Redis Stream lets us avoid. The Stream is **not** a langgraph-era vestige;
it is the live-event transport layer. **Non-goal:** changing the SSE transport.

## Goals

- A `cancel`/`steer` request landing on **any** instance reaches the instance
  that owns the run.
- No new infrastructure beyond Redis (already a hard dependency).
- Single-instance behavior is unchanged (same latency, same synchronous
  confirmation).
- Unify `cancel` and `steer` over one control mechanism.

## Non-Goals

- Changing the SSE transport (see rationale above).
- Strong delivery/consumption guarantees for cross-instance control (we accept
  fire-and-forget "accepted" semantics — see Confirmation).
- Moving run execution off the receiving instance (runs still execute where
  `POST /messages` lands).
- Sticky load-balancer routing.

## Design: Redis pub/sub control channel + local fast-path

Replace "find the local handle" with "deliver a signal to whichever instance
owns the run." The owner already holds the handles; it just needs to *hear*
control requests that arrived elsewhere.

### Control channel — one shared channel per deployment (B1)

A single Redis pub/sub channel for the whole deployment:

```
{key_prefix}:control
```

Message payload (JSON) carries the target `run_id`:

```json
{ "run_id": "<id>", "type": "cancel" }
{ "run_id": "<id>", "type": "steer", "content": "<user text>" }
```

Every instance subscribes to this **one** channel at startup and stays
subscribed for its whole lifetime; each instance filters incoming messages by
whether it owns the `run_id`. (See "Subscription topology" below for why a
single shared channel beats a per-run channel.)

pub/sub (not a Stream) is the right primitive: control signals are only
meaningful while the owner is alive and subscribed. If no instance owns the
`run_id` (run ended, or owner crashed), every instance's filter drops it — the
existing stale-run detection (`is_stale_meta` / `mark_run_stale`) handles a dead
owner.

### Owner-side listener — one per instance, lifetime-scoped

Each instance runs **one** listener coroutine for its entire lifetime (started
in the app lifespan / `RunManager` startup, stopped on shutdown), reading from
the single shared channel. It is NOT per-run — runs do not subscribe or
unsubscribe; they only register/deregister their handles in the existing
`_tasks` / `_agents` dicts, and the listener reads those:

- on `{"run_id", "type":"steer", "content"}` → if `run_id in self._agents`,
  call `agent.steer(UserMessage([TextContent(content)]))`; else ignore.
- on `{"run_id", "type":"cancel"}` → if `run_id in self._tasks`, call
  `self.cancel_run(run_id)` (cancels + awaits the local task, same as today);
  else ignore.

Because the listener runs in the owner process, it applies the signal through
the **same local code path** the endpoint uses today — it just sources the
request from Redis instead of an in-process call. The listener and the agent
share one event loop, so `agent.steer` (lock-free `list.append`) stays safe.

### Subscription topology: why one shared channel (B1), not per-run

A per-run channel (`…:run:{run_id}:control`) gives targeted O(1) delivery, but
its only advantage — not broadcasting to every instance — optimizes a dimension
that does not matter here: control messages are **discrete, human-initiated**
(a Stop click, a steer send), so the rate is low and broadcast fan-out is
negligible (e.g. 50 instances × 10 controls/s = 500 trivial deliveries/s).
Against that non-benefit, per-run costs real complexity: the subscription set
**churns** as runs start/stop (must be serialized across concurrent
start/stop), and there is a "run is active but not yet subscribed" window where
a control message would be lost.

A single shared channel makes the subscription **static** (subscribe once at
startup, never change), eliminating both the churn and that setup race, for the
cost of a local `run_id`-membership check per message. Note redis-py multiplexes
many channels onto one connection, so per-run would NOT cost extra connections —
the real per-run cost is churn, not connection count.

If control throughput or payload size ever grows enough that broadcast waste
matters, the upgrade path is **B2** (each instance subscribes to its own
`…:instance:{id}:control`, plus a Redis `run_id → instance_id` registry so the
publisher targets the owner). B2 keeps the static-subscription model and adds
targeted delivery at the cost of maintaining + stale-handling that registry.
We do not need it now.

### Endpoint behavior (local fast-path → cross-instance publish)

Both `cancel_active_run` and `steer_active_run` change to:

1. Look up the active run in Redis (already done today).
2. If no active/running run → return "no active run" (unchanged).
3. **Local fast-path:** if `run_id` is in this instance's `_tasks`/`_agents` →
   act directly and return synchronous confirmation (today's behavior, today's
   latency). The owner never publishes to itself → no double-application.
4. **Cross-instance:** otherwise `PUBLISH` `{run_id, type, ...}` to the shared
   `{key_prefix}:control` channel and return `202 Accepted` with "published"
   status. (The publishing instance also receives its own broadcast but its
   filter discards it — it published precisely because the run is not local.)

### Confirmation semantics (decided)

- **Local fast-path (both):** synchronous, authoritative — `steer` returns
  whether the agent accepted; `cancel` awaits cleanup (preserves the
  no-409-on-immediate-resend guarantee).
- **Cross-instance `steer`:** `202` "published" — delivery to the channel, not a
  consumption guarantee. No ack (steer has no resend race).
- **Cross-instance `cancel`:** **Option B (bounded ack)** — the endpoint waits
  for the owner's post-cleanup ack and returns `cancelled` (safe to resend) or,
  on timeout, `published` (best-effort). This preserves today's "cancel response
  ⇒ safe to resend" contract cross-instance. Mechanics pinned in "Cross-instance
  cancel ack" below; rationale + the rejected Option A in decision #2.

Proposed response shape (generalizes today's `{steered, run_id}`):

```json
{ "status": "steered" | "published" | "no_active_run", "run_id": "<id|null>" }
```

- `cancel`: `{ "status": "cancelled" | "published" | "no_active_run", "run_id" }`.
- HTTP: `202` for `steered`/`published`/`cancelled`; `200` for `no_active_run`.

**Frontend impact (steer):** the optimistic bubble currently rolls back when
`steered === false`. New rule: keep the bubble for `steered` **and**
`published`; roll back only for `no_active_run`. (`messageStore.steer` +
`SteerRunResponse` in `@cubeplex/core`.)

### Cross-instance cancel ack (Option B mechanics)

Pin the ack design so it doesn't hold a subscriber connection per request:

- **One ack subscriber per instance**, symmetric with the control listener:
  each instance subscribes once at startup to a single shared ack channel
  `{key_prefix}:control:ack` and keeps an in-process map `run_id → asyncio.Future`.
- A cross-instance `cancel` endpoint: (1) register a `Future` under `run_id` in
  that map, (2) `PUBLISH` the cancel to `{key_prefix}:control`, (3)
  `await asyncio.wait_for(future, timeout≈2–3s)`. **Register before publish** so
  the ack can't arrive before we're waiting (the ack lands as a Future
  resolution, not a missed message). Always remove the map entry in a `finally`.
- The **owner**, after `cancel_run` cleanup completes (active-run key cleared),
  `PUBLISH`es `{run_id}` to `{key_prefix}:control:ack`.
- Each instance's ack listener resolves the matching `Future` (by `run_id`) if
  present; unknown `run_id`s are ignored (the requester is on another instance
  or already timed out).
- Future resolved → endpoint returns `cancelled`; `wait_for` timeout → returns
  `published` (owner slow or gone).
- **Waiter map = `run_id → list[Future]`** (a list, so two concurrent cancels of
  the same `run_id` on one instance each get their own future; one ack resolves
  all). **Cleanup removes only this request's future**, and pops the `run_id`
  key only when its list becomes empty — a timed-out waiter must NOT erase the
  whole entry (that would orphan sibling waiters / a later valid ack). No
  per-request Redis subscription is created.

This keeps cross-instance cancel to **two extra pub/sub messages** (cancel +
ack) and zero per-request connections.

### Connection robustness (both listeners)

The control listener and the ack listener are long-lived pub/sub subscribers. A
dropped Redis connection must **reconnect and re-subscribe** — otherwise an
instance silently goes deaf to control/acks after a blip while still serving
traffic (a production multi-instance hazard). Requirements:

- **Reconnect with backoff + re-`SUBSCRIBE`** on any connection error, then
  resume the read loop.
- **Per-message exception containment:** wrap each message's decode + handler in
  `try/except` so one bad payload (malformed JSON, schema drift) or a handler
  fault logs and is skipped — it must NOT break out of the read loop and kill
  the long-lived listener (which would silently deafen the instance until
  restart). Only connection-level errors trigger the reconnect path.
- **Await readiness on startup:** `start_control_listeners` must not return until
  the first `SUBSCRIBE` for **both** channels has completed (each loop signals an
  `asyncio.Event` after subscribing; startup awaits both, **bounded** by a short
  timeout so a Redis hiccup can't hang boot). Otherwise the listeners are
  fire-and-forget and a control published in the gap between "app ready" and
  "subscriber live" is silently lost.
- **Lifecycle = app lifespan:** start on startup (after readiness); on shutdown,
  stop them **after** in-flight runs drain (so graceful drain can still receive
  controls).

## Failure modes & edge cases

- **Owner crashed after a steer/cancel was published:** no subscriber consumes
  it; message dropped. The run is already dead; `is_stale_meta` marks it stale
  on the next bootstrap/stream read. Acceptable.
- **Cross-instance cancel + immediate resend (409 race):** today `cancel_run`
  awaits task cleanup so a resend doesn't hit "already has an active run."
  Cross-instance, the publishing endpoint can't await the remote task, so a
  fast resend may briefly 409 until the owner finishes cleanup. Addressed by the
  recommended bounded-ack (Option B, Open question #2), which restores the
  "cancel response ⇒ safe to resend" contract; Option A would instead rely on a
  client 409-retry / active-run poll.
- **Double delivery:** the owner uses the local fast-path and never publishes to
  itself, so a signal is applied at most once. Every instance receives the
  broadcast but only the owner's `run_id` filter matches. pub/sub at-most-once
  delivery is fine here.
- **Request lands on owner but run just ended:** local lookup misses → publishes
  → its own (about-to-unsubscribe) listener may or may not catch it; if not, the
  run is over anyway.
- **Steer ordering:** cubepi drains the steering queue at safe points; multiple
  steers queue in arrival order per the existing `_steering_queue` (one-at-a-
  time). Cross-instance ordering is best-effort (pub/sub delivery order).
- **Handle-registration readiness window (cancel/steer asymmetry):** `start_run`
  puts the task into `_tasks` immediately, but `_agents[run_id]` is only set deep
  in `_run_cubepi_path` after the agent (LLM + tools + sandbox) is built — a
  multi-second gap during which the run is already "active" in Redis. So a
  control arriving in that window: **cancel works**, but **steer is dropped**
  (owner has no `_agents[run_id]` yet → filter misses).

  Why cancel is safe even though `create_run` commits the active-run key
  *before* the `_tasks` assignment (`run_manager.py:462-493`): on the owner's
  single event loop there is **no `await` between** `create_run` resolving and
  `self._tasks[run_id] = task`, so that assignment completes in the same
  synchronous slice — the control listener (another task on the same loop)
  cannot run until `start_run` next yields, by which point `_tasks` already has
  the id. A cross-instance cancel can only reach the owner after a Redis
  round-trip (B observes the key → publish → owner listener), which necessarily
  happens after that slice. So the owner never processes a cancel while `_tasks`
  lacks the run. (`_agents` is different: it's set deep inside `_run_cubepi_path`
  after many awaits, hence the steer window is real.)
  This is not multi-instance-specific — single-instance steer in this window also
  no-ops via the fast-path (`steered:false` → `no_active_run`/`published`); there
  is simply no agent to steer yet. Documented, not fixed: the frontend treats it
  as a transient no-op (the user can re-send the steer once the run is underway).
- **Owner crash blocks the conversation until stale-detection fires:** if the
  owning instance dies mid-run, its active-run key persists until `is_stale_meta`
  marks it stale (`lifecycle.stale_run_threshold_seconds`, default 120s). During
  that window controls publish to nobody (dropped) and a new `send` 409s
  (active-run held). Acceptable; operators can lower the threshold. No new
  mechanism added here — this is existing single-instance crash behavior,
  unchanged by multi-instance.

## Security / scoping

No change to the auth model. Endpoints stay workspace-scoped
(`/api/v1/ws/{ws}/conversations/{id}/{cancel,steer}`) and verify membership +
conversation ownership before publishing. The control channel key includes the
Redis `key_prefix` (already per-deployment/per-worktree isolated). `run_id` is
an opaque uuid7; we still gate on the conversation's active-run record, so a
caller can't steer an arbitrary run_id. With the shared channel (B1) every
instance receives every control payload (including steer text) within the
deployment — this stays inside the trusted backend tier and the same
`key_prefix` namespace; secure the Redis link (TLS/authn) as for all other run
state. If broadcasting user text to all instances is later deemed too exposed,
B2's targeted delivery removes it.

## Testing strategy

- **Unit (fakeredis — already a dev dep):** publish a `steer`/`cancel` message
  to a run's control channel; assert the listener dispatches to a fake agent /
  cancels a fake task. Assert the endpoint takes the local fast-path when the
  handle is present and publishes when absent.
- **Integration (two `RunManager`s, one shared Redis):** instance A starts a run
  (registers handle + subscribes); instance B's endpoint publishes a steer;
  assert A's agent receives it. Simulates cross-instance without real HTTP.
- **Cancel ack round-trip:** B registers a future + publishes cancel; A's
  `cancel_run` cleanup publishes the ack; assert B resolves to `cancelled`.
  Separately assert `wait_for` timeout (no owner / slow owner) returns
  `published`. Assert the per-`run_id` waiter map entry is cleaned up in both
  paths.
- **Listener reconnect:** assert the control/ack listeners re-`SUBSCRIBE` after a
  simulated connection drop (fakeredis or a stubbed pubsub that raises once),
  i.e. an instance keeps receiving after a blip.
- **E2E (real-LLM, single instance):** unchanged — the existing
  `test_steer_endpoint.py` still passes via the local fast-path.
- Cross-instance E2E with two live uvicorn processes is out of scope for the
  test suite (no fake for a real LB); the integration test above covers the
  dispatch logic.

## Rollout / compatibility

- Single-instance deployments are unaffected (fast-path only; the listener still
  runs but never receives cross-instance messages).
- No schema/migration changes. No new dependency.
- Backward-compatible API: the response gains a `status` field; the old
  `steered` boolean can be retained as `status === "steered"` during a
  transition if needed, or the frontend updated in lockstep (single repo).

## Decisions

1. **Subscription topology — DECIDED: B1 (single shared channel + local
   `run_id` filter).** Control messages are low-rate and human-initiated, so
   per-run's targeted delivery buys nothing meaningful, while a static
   per-instance subscription removes per-run churn and the subscribe-setup race.
   B2 (per-instance channel + `run_id → instance_id` registry) is the documented
   upgrade path if throughput/payload ever makes broadcast waste matter. See
   "Subscription topology" above.

## Further decisions

2. **Cross-instance `cancel` confirmation — DECIDED: Option B (bounded ack).**
   This concerns only
   `cancel`, not `steer` (steer doesn't claim the per-conversation active-run
   mutex, so it has no analogous race).

   *The race, precisely.* `start_run → create_run` claims the conversation's
   active-run key in Redis; while it's held, a second `start_run` for the same
   conversation raises → the API returns **409**. Today `cancel_run` does
   `task.cancel()` **then `await task`**, so it blocks until the run task's
   `finally` has run `clear_active_run` (released the key). That's why, on a
   single instance, "click Stop, immediately resend" is safe: the cancel
   response only returns *after* the key is free, so the resend's `create_run`
   succeeds.

   *Why cross-instance regresses it.* When the cancel lands on instance B, B
   publishes and returns `202` **immediately**; the owner A receives it and runs
   `cancel_run` (which awaits cleanup **on A**), but B never waited for A. So
   between B's `202` and A finishing `clear_active_run` there is a window where a
   fast resend's `create_run` can still see the key held → **409**. The window
   is roughly A's teardown time (Redis key clear is quick, but `_execute_run`'s
   `finally` also releases the sandbox / closes sessions, so it can be a few
   hundred ms up to seconds). Single-instance keeps the synchronous guarantee
   via the local fast-path; only the cross-instance path loses it.

   *Why not just clear the key from B.* Eagerly clearing the active-run key from
   the cancel endpoint would let a new run start while A's task is still alive
   and streaming into the same conversation's Redis stream + checkpointer —
   concurrent runs per conversation, interleaved events. The key is the mutex;
   clearing it early is unsafe. Rejected.

   *Option A — accept v1 fire-and-forget.* Cross-instance cancel returns `202`
   without the cleanup-wait guarantee; a too-fast resend may `409`. Mitigate on
   the client: treat a post-cancel `409` as "previous run still finishing" and
   auto-retry after a short backoff (or gate the resend on `bootstrap.active_run`
   going `null`) instead of surfacing a hard error. Cheapest; the race is narrow
   and recoverable.

   *Option B — bounded ack round-trip.* Restore the synchronous guarantee
   cross-instance. The mechanics are pinned in "Cross-instance cancel ack" above
   (one **startup** ack subscriber per instance + a `run_id → list[Future]` map;
   the endpoint **registers a future before publishing**, publishes the cancel,
   then `await asyncio.wait_for(future, timeout≈2–3s)`; the owner publishes the
   ack after its `cancel_run` cleanup clears the active-run key). B returns
   `cancelled` on ack — safe to resend — or `published` on timeout (best-effort;
   owner slow or gone); both are HTTP `202` (`200` is only `no_active_run`). Cost
   is two extra pub/sub messages per cross-instance cancel and **no** per-request
   subscription. Surgical, cancel-only, and it preserves today's behavior exactly
   when the owner is responsive.

   *UX difference (this is the deciding lens).* In almost every interaction A
   and B are indistinguishable — they differ ONLY in the corner "multi-instance
   deployment + user clicks Stop then **immediately** sends a new message,
   landing inside the owner's teardown window (sub-second to a couple seconds)."
   Single-instance (local fast-path) and all of `steer` behave identically under
   both.

   | User action | Option A (202, no ack) | Option B (bounded ack) |
   |---|---|---|
   | Click Stop | UI flips to "stopped" instantly (optimistic, backend-independent) — same both | same |
   | Stop, **pause** (read/think/type ≥1–2s), then send | window already closed → send works | send works |
   | Stop, **instantly** send | `create_run` hits the still-held active-run lock → **409**. Without client mitigation: a confusing "send failed / HTTP 409", user thinks the message was lost, must resend. With mitigation (retry-on-409 / poll `active_run`→null): no error, but the resend spins for a few hundred ms–seconds | "cancel response ⇒ safe" contract holds; gate resend on the cancel call → **send just works, no error, no delay perceived** |
   | Cancelled turn's partial output | preserved (finalize-on-cancel; independent of A/B) | same |

   Engineering implication: **B needs ~no frontend change** (keeps the existing
   "await cancel ⇒ safe to resend" contract); **A pushes work to the frontend**
   (must add 409-retry or active-run polling) and still leaves a corner error if
   that mitigation is skipped.

   *Decision: **Option B.*** "Stop, then immediately rephrase and resend" is a
   common agent-chat interaction (user interrupts, redirects), so the one
   regression A introduces is worth removing outright. B's cost is a small,
   cancel-only ack round-trip on the backend; in return the UX matches
   single-instance exactly and the frontend is untouched. (`steer` still needs
   no ack.)

3. **Response shape — DECIDED: adopt the unified `status` field for BOTH
   `cancel` and `steer`, replacing today's booleans.**

   *Why a boolean is no longer enough.* Today `cancel` returns
   `{cancelled: bool, run_id}` and `steer` returns `{steered: bool, run_id}`.
   Once cross-instance exists, each control action has **three** distinct
   outcomes that a boolean can't express without overloading:
   - acted locally / confirmed (run is definitively handled here, or — for
     cancel under Option B — ack-confirmed cleaned up),
   - published to the owner but **not confirmed** (cross-instance; for cancel
     under B this is the ack-timeout case, for steer it's always the
     cross-instance case),
   - there was **no active run** to act on.

   Cramming "published-but-unconfirmed" into `cancelled: false` would make it
   indistinguishable from "no active run," and the frontend can't then tell
   "safe to resend now" from "still settling." So we move both to:

   ```json
   // steer
   { "status": "steered"  | "published" | "no_active_run", "run_id": "<id|null>" }
   // cancel
   { "status": "cancelled" | "published" | "no_active_run", "run_id": "<id|null>" }
   ```

   *Status meanings.*
   - `steered` / `cancelled` — local fast-path, or (cancel + Option B)
     ack-confirmed. Authoritative; for cancel, resend is safe.
   - `published` — broadcast to the owner, consumption not confirmed (steer
     cross-instance always; cancel cross-instance only on ack timeout).
   - `no_active_run` — nothing to act on.

   HTTP: `202` for `steered`/`cancelled`/`published`; `200` for `no_active_run`.

   *Frontend use (ties OQ#3 to OQ#2/B).*
   - steer: keep the optimistic bubble for `steered` **and** `published`; roll it
     back only on `no_active_run`.
   - cancel: gate "safe to resend immediately" on `status === "cancelled"`; on
     `published` (B ack timeout) fall back to the active-run poll / retry-on-409
     before resending. This is exactly the signal Option B provides.

   *Migration.* Single repo, frontend + backend land together; cubeplex hasn't
   shipped publicly, so cut the booleans (`cancelled` / `steered`) over cleanly
   to `status` — no back-compat shim. Touch points: backend `cancel_active_run`
   + `steer_active_run`; `@cubeplex/core` `CancelRunResponse` / `SteerRunResponse`
   types + `cancelStream` / `steer` store actions (the steer rollback rule and
   the cancel resend-gating above).
