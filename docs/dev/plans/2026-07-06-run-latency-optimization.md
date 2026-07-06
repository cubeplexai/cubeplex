# Run latency optimization — send→first-token & last-token→done

- **Date**: 2026-07-06
- **Branch**: `feat/2026-07-06-run-latency`
- **Status**: in progress

## Problem

Users see two long dead-air windows on every chat turn:

1. **Send → first assistant token.** After the optimistic user message
   renders, nothing happens on screen until the LLM's first `text_delta`
   arrives. All backend preparation work sits in this window, serialized,
   and emits no SSE events.
2. **Last token → input re-enabled.** The frontend keeps `isStreaming`
   until the `done` event; everything the backend does between the final
   `text_delta` and `DoneEvent` extends this window.

## Measured/traced causes (from code walk, to be confirmed by baseline)

Window 1, in execution order:

| # | Cost | Where |
|---|---|---|
| C1 | ~7 sequential short-lived DB sessions in the route (conv lookup, auto-join, attachment check, snapshot load #1, topic ctx, mark_attached, timestamp bump) | `conversations.py::send_message` |
| C2 | `init_checkpointer()` creates a **fresh asyncpg pool** (TCP+auth+schema check) per call; ≥2 creates per send (`start_run` pending check, `_run_cubepi_path`) | `agents/checkpointer.py:31`, `run_manager.py:926,1596` |
| C3 | `load_llm_snapshot` runs **twice** per send (route validation + `_execute_run`), each decrypting all provider credentials | `conversations.py:1425`, `run_manager.py:3461` |
| C4 | Full conversation history loaded **twice** (citation seed `cp.load`, then cubepi `Agent.prompt` internal load) | `run_manager.py:1604`, cubepi `agent.py:510` |
| C5 | **MCP eager load: live `initialize` + `tools/list` round-trip to every enabled server, sequentially, every send** — even though `tools_cache` (name/description/input_schema/output_schema) is already persisted on the install row. A slow server blocks up to `spec.timeout`. | `run_manager.py:2612` → `mcp/cubepi_runtime.py:126` |
| C6 | No status events during build — `emit_status` exists but only fires on `sandbox_failed`, so the frontend `statusPhase` stays null | `run_manager.py:3307` |

Window 2, in execution order (all before `DoneEvent`):

| # | Cost | Where |
|---|---|---|
| C7 | `_update_conversation_timestamp` + `_bump_topic_activity` + `_enqueue_search_index`: three sequential DB/Redis writes that don't need to precede `done` | `run_manager.py:3551-3568` |
| C8 | `get_session_usage`: SUM+JOIN over **all** billing rows of the conversation, recomputed every turn, grows with conversation length | `services/usage.py:49` |
| C9 | In `finally`, sandbox release (a remote opensandbox call) runs **before** `clear_active_run` — after `done` the user can still hit 409 on the next send until release finishes | `run_manager.py:3761-3800` |

Non-goals: LLM provider TTFT itself (model choice, prompt-cache hit rate)
is out of scope except for not regressing the cache prefix; the
reflection/consolidation background tasks are already detached and stay
as-is.

## Tasks

### T0 — Baseline measurement harness

Script `backend/scripts/dev/measure_run_latency.py`: sends a message via
the HTTP API with `Accept: text/event-stream`, records wall-clock marks —
request-sent, first SSE event, first `text_delta`, last `text_delta`,
`done` — and prints one row per run. Run N=5 against (a) a fresh
conversation, (b) a conversation with long history and MCP connectors
enabled. Store outputs in `tmp/latency-baseline.txt`.

**Verify**: script runs against the dev backend with the test API key;
numbers are plausible and stable enough to compare (±20%).

### T1 — MCP tools from `tools_cache` (biggest win, C5)

In `_build_agent_for_conversation`'s eager branch, replace
`_load_tools_for_specs` with a cache-based builder:

- New `_build_tools_from_cache(specs, all_specs, auth...) ` in
  `mcp/cubepi_runtime.py`: for each spec with non-empty `tools_cache` and
  `discovery_status == "ok"`, resolve auth exactly as today
  (`_resolve_auth_from_spec` — local DB/Redis work, no MCP round-trip),
  then build tools via cubepi `make_mcp_agent_tool(name, description,
  input_schema, call_remote)` with the same per-call fresh-session
  `_call_remote` the http_loader uses (tool calls already open a fresh
  session per call — cubepi v1 semantics — so skipping discovery changes
  nothing at call time). Namespacing/citation logic reused unchanged.
- Specs with an empty/stale cache fall back to today's live load, but all
  per-server loads (cache misses) run under `asyncio.gather`.
- Staleness: after the tool list is built, if `last_discovered_at` is
  older than `mcp.tools_cache_ttl_hours` (default 24), fire a detached
  refresh task reusing the existing discovery service. Never blocks the
  run.
- Note: `make_mcp_agent_tool` lives in `cubepi.mcp._adapter` and isn't in
  `__all__` — either import from the private module with a comment or add
  a small wrapper upstream later.

**Verify**: unit test for `_build_tools_from_cache` (tool schema equals
what the live loader would produce for the same cache content); e2e:
enable ≥1 MCP connector, send message, assert tools usable and no
`tools/list` request hits the server during send (assert via server-side
counter in the test MCP fixture); baseline re-run shows window-1 drop.

### T2 — Process-level shared checkpointer pool (C2)

`agents/checkpointer.py`: add a module-level shared
`PostgresCheckpointer` opened once (lazily on first use, or explicitly in
the app lifespan) and a `shared_checkpointer()` async context manager
that yields it without closing. Migrate the hot-path call sites
(`run_manager.py` ×7, `conversations.py` ×6, `repositories/conversation.py`,
`im/resume.py`, `services/conversation_sharing.py`) to the shared
instance. `init_checkpointer()` stays for scripts/workers/tests that need
an isolated pool. Lifespan shutdown closes the shared pool after the run
manager drains.

**Verify**: existing e2e suite for conversations/runs passes; grep shows
no hot-path `init_checkpointer()` left; log pool-create count during one
send == 0 (pool pre-created).

### T3 — Load once, reuse: LLM snapshot & history (C3, C4)

- Route → run: `send_message` passes its validated `LLMSnapshot` through
  `start_run(..., llm_snapshot=snap)` into `_execute_run`'s
  `extra_ref_holder["llm_snapshot"]` (the reuse mechanism inside the run
  already exists; this closes the route→run gap). Same for the
  `_execute_respond_run` path where applicable.
- History: `_run_cubepi_path` already does `cp.load()` for the citation
  seed. Hand that loaded state to the agent — set `agent.messages` /
  restore `extra` from the same `CheckpointData` (cubepi `AgentState`
  exposes a public messages setter; `prompt()` skips its internal load
  when messages are non-empty). One full-history load per send instead of
  two.

**Verify**: unit test that the snapshot object identity is preserved
route→run; e2e multi-turn conversation still replays correctly (history
not duplicated/lost — assert message count via list_messages); prompt
prefix stability spot-check per prompt-cache-discipline (cache_read
tokens non-zero on turn 2 with Anthropic-format provider).

### T4 — Slim the pre-`done` tail (C7, C8)

- ~~Redis running totals~~ **Revised during implementation**: the SQL SUM
  stays (it is already approximate — `CostMiddleware._write` is a
  fire-and-forget task racing `done`, and a Redis counter incremented in
  the same detached write would carry the identical race), but it now
  runs as an `asyncio.Task` kicked **before** the queue drain, so the
  tail pays `max(drain, usage-query)` instead of their sum.
  `ix_billing_events_conversation` already indexes the lookup. This
  avoids adding a cache-consistency surface to billing for the same
  observable accuracy.
- Reorder: append `DoneEvent` right after the queue drain; move
  `_update_conversation_timestamp` / `_bump_topic_activity` /
  `_enqueue_search_index` after it (search-index enqueue still happens
  after cubepi finished writing history — that ordering constraint is
  about the checkpointer write, which completes inside `agent.prompt()`,
  not about `DoneEvent`). Post-done failures log instead of emitting SSE
  error events (the stream is already closed).

**Verify**: e2e run asserts done-event usage matches SQL SUM; baseline
re-run shows window-2 drop; existing search-index e2e still passes.

### T5 — Status events during build (perceived latency, C6)

Emit `emit_status` phases in `_execute_run` / `_run_cubepi_path`:
`preparing` (entry), `loading_tools` (before MCP/action tools),
`starting` (right before `agent.prompt`). Frontend already stores
`statusPhase` (`messageStore.ts:620`) — render a subtle phase hint in the
assistant placeholder (i18n keys for zh/en). Docs page for the chat flow
updated in the same PR (docs-ship-with-code rule) if any user-facing doc
describes the loading behavior.

**Verify**: Playwright: after send, the placeholder shows a phase hint
before the first token (only asserted as a step inside the existing
chat-flow test, not a standalone presence test).

### T6 — Release the turn lock before sandbox release (C9)

In `_execute_run`'s `finally`, move `clear_active_run` +
`expire_run_data` ahead of the sandbox release block (keep the
`paused_hitl` guard exactly as-is). Sandbox release cannot affect the
next turn's correctness — LazySandbox re-acquires by scope, and release
is already best-effort (`suppress(Exception)`).

**Verify**: e2e: send turn A with a sandbox-using tool, immediately send
turn B on `done` — no 409. Existing pause/resume sandbox e2e still
passes.

### T7 — Re-measure & compare

Re-run T0's script against the worktree backend on the same infra/DB;
produce a before/after table for both windows (fresh + long conversation,
MCP on). Full pre-PR sweep (`make check-ci` equivalent + changed-module
e2e).

**Results (2026-07-06, fresh conversation, 5 runs each, no MCP connectors
installed, dev DB via 215 tunnel ~210ms RTT):**

| metric | baseline | optimized | delta |
|---|---|---|---|
| first backend feedback | 64.1s (== first token; no early events) | ~3s (`preparing`) | dead air removed |
| time to first token | 64.1s | 51.4s | −20% |
| time to `done` | 97.1s | 73.5s | −24% |
| tail (last token → done) | 17.2s | 19.8s | ~flat |

Per-run internal decomposition (Redis stream timestamps, one run) reveals
where the remaining time sits — the send-side is now dominated by two
costs **outside** what this change set targeted:

| phase | cost | note |
|---|---|---|
| preparing → loading_tools | ~3s | route prep + early run setup |
| **loading_tools → starting** | **15–23s** | `_build_agent_for_conversation` — serial DB round-trips |
| starting → first token | 12–23s | LLM TTFT via the litellm proxy |
| last token → usage event | ~7s | proxy stream finalization |
| usage → done | ~14s | cubepi prompt finalization + drain + done |

**Interpretation.** The wins that landed: dead-air before first feedback
is gone (T5), and the removed-round-trip changes (shared checkpointer
pool, snapshot reuse, single history load) plus the post-`done`
bookkeeping move cut ~24s off total `done` time. What the decomposition
exposes as the *next* bottleneck is `_build_agent_for_conversation`
(15–23s) — and this workspace has **no MCP connectors**, so T1's cache is
not even exercised here; the cost is the sheer count of serial DB session
opens in the build path, each paying `pool_pre_ping` (`SELECT 1`, ~210ms)
+ the actual query (~210ms) under the 210ms tunnel RTT. Provider TTFT and
the proxy's stream finalization (the 7s usage lag + tail) are
environmental (litellm proxy on 215) and out of code scope.

The 210ms RTT is a **test-environment artifact** (VM → VPN → 215). A
co-located production Postgres (<5ms RTT) shrinks every serial-DB cost by
~40×, so the absolute seconds here overstate the real deployment, while
making the round-trip-count reductions the most valuable thing to verify.

### T8 (follow-up, not in this branch) — collapse the agent-build DB work

**Verdict after co-located re-measurement (2026-07-06): NOT worth doing.**

Re-ran the A/B on 215 where Postgres/Redis are co-located with the app
(<5ms RTT vs the 210ms dev tunnel):

| phase | dev tunnel (210ms) | 215 co-located (<5ms) |
|---|---|---|
| prep → loading_tools | ~3s | 0.02–0.04s |
| build (loading_tools → starting) | 17–23s | **2.0–4.5s** |
| TTFT (LLM) | 12–23s | 6–14.5s |
| tail | 15–21s | 6–7.8s |

End-to-end on 215: first feedback 21.6s → **0.22s** (status events),
first token 21.6s → 14.8s, done 29.5s → 21.3s, tail 7.3s (flat).

The build's ~85% collapse (17–23s → 2–4.5s) confirms the tunnel RTT was
the amplifier the three DB-oriented changes would have targeted — and it
is a test-environment artifact, not production. The residual 2–4.5s build
cost **persists at <5ms RTT**, so it is not round-trip-count bound; it is
CPU/setup bound (constructing 21 tool schemas + 11 middleware per send,
credential/snapshot crypto). Dropping `pool_pre_ping`, merging sessions,
or `asyncio.gather` all target I/O-wait, which is already near-zero on a
co-located DB — they would move the production number by tens of
milliseconds, against a real reliability cost (pre_ping) and added
complexity. **Skip them.**

If the residual 2–4.5s build is ever pursued, it needs CPU profiling of
tool/middleware construction (cache tool schemas across turns; the tool
set is stable per workspace), not DB-session surgery — a different effort
with uncertain payoff, since build is ~15–20% of a TTFT dominated by the
6–14s LLM call.

## Sequencing & PR split

T0 first (baseline before any change). Then T2 → T3 → T1 → T4 → T6 → T5
(shared-pool and reuse changes are low-risk enablers; MCP cache is the
big one; T5 touches frontend last). One PR for the backend latency work
(T1–T4, T6, tightly coupled by run_manager), a separate small PR for T5
if the frontend change grows; plan doc rides with the first PR.

## Risks

- **Stale MCP tool schema** (T1): a server changed its tools since
  discovery → model calls a tool with an outdated schema. Mitigated by
  TTL-triggered background refresh + fallback to live load when cache is
  empty; call-time errors surface to the model as tool errors (same as
  today's mid-conversation server drift).
- **Prompt-cache prefix**: T1/T3 must not change tool ordering or
  serialized tool schemas (cache-built tools must serialize byte-identical
  to live-built ones — covered by the T1 unit test).
- **Shared pool sizing** (T2): one pool now serves all concurrent runs;
  keep max_pool_size configurable (`database.cubepi_pool_max`, default
  10) and watch for exhaustion in the drain logs.
- **Usage hash drift** (T4): Redis increments can lag the DB on crash;
  done-event usage is display-only, and the SQL fallback bounds the error
  to one turn.
