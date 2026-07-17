# cubepi Migration M3 — Middleware Ports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Port cubeplex's 11 LangChain `AgentMiddleware`s to cubepi's `Middleware` protocol so the cubepi runtime has behavior parity with the langgraph path. Each new file lives at `cubeplex/middleware/<name>_pi.py` alongside the existing langgraph version. After M3, the cubepi agent has: memory injection, sandbox lifecycle, skill content injection, subagent dispatch, citations, compaction, cost tracking, timestamps, todo discipline, artifact registry, and attachment rendering — all running through cubepi hooks.

**Architecture:** Each middleware is a class implementing `cubepi.middleware.Middleware`. The hook signatures are:
- `transform_context(messages, *, signal) -> messages` (chain)
- `transform_system_prompt(system_prompt, *, signal) -> str` (chain; D7 from Spec A)
- `before_tool_call(ctx, *, signal) -> BeforeToolCallResult | None`
- `after_tool_call(ctx, *, signal) -> AfterToolCallResult | None`
- `after_model_response(response, ctx, *, signal) -> TurnAction | None` (chain; D8 from Spec A)
- `should_stop_after_turn(state)` (existing cubepi hook; not used by cubeplex)

State channels formerly on `CubeplexState` (`memory_snapshots`, `compaction`, `compaction_until_msg_index`, 6 todo channels) now live in `ctx.extra` for singletons, OR in user-message `metadata` for per-message immutable data (per Spec B § "State migration").

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M3 + mapping table.

**Baseline:** M2 done; 524 unit tests pass; cubepi runtime end-to-end with calculator tool call confirmed (M2.6).

---

## Middleware → cubepi hook mapping (single source of truth)

| middleware | Agent.tools | transform_context | transform_system_prompt | before_tool_call | after_tool_call | after_model_response |
|---|---|---|---|---|---|---|
| Artifact | ✅ | ✅ | — | — | — | — |
| Attachment | — | ✅ | — | — | — | — |
| Citation | — | — | — | — | ✅ | — |
| Memory | — | ✅ | — | — | — | — |
| Compaction | — | ✅ | — | — | — | — |
| Sandbox | ✅ | — | ✅ | — | — | — |
| Skills | — | — | ✅ | — | — | — |
| SubAgent | ✅ | — | — | — | — | — |
| Cost | — | — | — | — | — | ✅ |
| Timestamp | — | ✅ | — | ✅ | ✅ | ✅ |
| Todo | ✅ | ✅ | ✅ | — | ✅ | ✅ |

`should_stop_after_turn` is preserved as cubepi feature; cubeplex doesn't use it.

---

## File map

Each port creates one new file `cubeplex/middleware/<name>_pi.py` + a test file. Plus a final integration commit wiring all `*_pi` middleware into `graph_pi.py` / `run_manager._run_cubepi_path`.

| File | Lines (est) | Hooks |
|---|---|---|
| `middleware/artifacts_pi.py` | ~150 | tools + transform_context |
| `middleware/attachments_pi.py` | ~80 | transform_context |
| `middleware/citation_pi.py` | ~150 | after_tool_call |
| `middleware/memory_pi.py` | ~250 | transform_context (most complex; cache discipline) |
| `middleware/compaction_pi.py` | ~120 | transform_context + ctx.extra |
| `middleware/sandbox_pi.py` | ~100 | tools + transform_system_prompt |
| `middleware/skills_pi.py` | ~80 | transform_system_prompt + ctx.extra |
| `middleware/subagents_pi.py` | ~180 | tools (the subagent tool spawns inner cubepi.Agent) |
| `middleware/cost_pi.py` | ~120 | after_model_response |
| `middleware/timestamps_pi.py` | ~80 | transform_context + before/after_tool_call + after_model_response |
| `middleware/todo_pi.py` | ~500 | full hook surface + ctx.extra |

---

## Tasks

### Pre-flight: M3.0

- [ ] Baseline: `cd backend && uv run pytest tests/unit -q --tb=no` → 524 pass
- [ ] E2E baseline: `uv run pytest tests/e2e/test_cubepi_path_*.py -v -m real_llm` → 2 pass

### Batch M3a — Simple transform_context / after_tool_call (3 tasks)

**M3.a.1** `attachments_pi.py` — `transform_context` chain renders `file_attachment` blocks from user message `metadata.attachments` as text. Port logic from existing `attachments.py`. Tests: unit only.

**M3.a.2** `artifacts_pi.py` — Two parts:
- Tool injection (artifact create/update functions as `cubepi.AgentTool`)
- `transform_context` renders artifact registry state for the model

Mirror `artifacts.py`. Tests: unit on the rendering + tool behavior.

**M3.a.3** `citation_pi.py` — `after_tool_call` hook reads tool result, extracts citations per `CitationConfig`, attaches to response.metadata. Mirror `citations/middleware.py`. Tests: feed mock tool result, assert citations extracted.

### Batch M3b — State-coupled (2 tasks)

**M3.b.1** `memory_pi.py` — **The big one**. Port `MemoryMiddleware`:
- `transform_context` reads pinned memory from MemoryRepository + relevance snapshots from user message metadata (per Spec B § "State migration")
- Snapshot WRITE happens at user message append time (caller responsibility, M3.b coordinates with `wire_input_to_cubepi_user_message` to inject snapshot)
- Cache discipline must hold: byte-identical pinned + interleaved snapshot rendering

Coordinate with `wire_input_to_cubepi_user_message` (convert_pi.py M1.2): add a memory-resolver hook so when a UserMessage is built for an inbound API request, the relevance snapshot is computed once and stuffed into `metadata["memory_snapshot"]`. Pure write at construction time.

**M3.b.2** `compaction_pi.py` — `transform_context` reads `ctx.extra["compaction"]` summary + `compaction_until_msg_index`, builds compressed view, writes new summary on threshold breach (via `ctx.extra` mutation; loop persists via save_extra).

### Batch M3c — System prompt / tool injection (3 tasks)

**M3.c.1** `sandbox_pi.py` — `transform_system_prompt` appends sandbox capability text; `tools` provides sandbox-injecting tools (e.g. python_exec). Mirror `sandbox.py`.

**M3.c.2** `skills_pi.py` — `transform_system_prompt` reads `ctx.extra["loaded_skills"]` list and appends their content. Coordinates with `load_skill_pi` (M2.3): after a `load_skill` tool call, populate `ctx.extra["loaded_skills"]` (via `after_tool_call` hook on skills_pi watching for `tool_call.name == "load_skill"`).

**M3.c.3** `subagents_pi.py` — Inject a `subagent` `AgentTool` whose `execute()` spawns an inner `cubepi.Agent` and streams events back to the parent via the `subagent_event_queue` ContextVar (already exists, reused). Much simpler than the spike since both parent + inner are cubepi.

### Batch M3d — Telemetry (2 tasks)

**M3.d.1** `cost_pi.py` — `after_model_response` reads `response.usage` (input/output tokens, cache hit/write tokens), writes billing record. Same DB surface as langgraph `CostMiddleware`; reuse impl body where possible. Carry `_subagent_depth`, `_parent_billing_id` like the langgraph version.

**M3.d.2** `timestamps_pi.py` — Stamp timing data:
- `transform_context`: turn-start timestamp
- `before_tool_call` / `after_tool_call`: tool start/end timestamps
- `after_model_response`: turn-end timestamp; also stamp `created_at` on the response.metadata

### Batch M3e — Todo (1 task, large)

**M3.e** `todo_pi.py` — port full TodoListMiddleware:
- `tools`: write_todos tool
- `transform_system_prompt`: append write_todos instructions
- `transform_context`: render current todos (from `ctx.extra["todos"]`)
- `after_tool_call`: catch write_todos invocation, update `ctx.extra["todos"]`
- `after_model_response`: full guard logic (parallel calls error, validation errors, stale reminder, finalization guard, blocked state machine) returning `TurnAction(inject_messages=..., decision=...)`

This is the largest single port. Reuse private helpers from existing `todo.py` aggressively.

### Wire-up: M3.f

Update `graph_pi.py` and `run_manager._run_cubepi_path` to compose all middleware into the `cubepi.Agent` constructor `middleware=[...]` list. Ordering matters (see existing langgraph stack order in `graph.py`).

### Validation: M3.g

- Existing M1.6 / M2.6 E2E pass unchanged
- New E2E exercising memory snapshot + cache discipline: turn N reaches the model with the same byte prefix as turn N+1
- Full unit suite green: ~524 + N new tests (estimate ~80-150 across batches)
- Manual smoke: a 3-turn conversation that:
  1. references something the model should remember (writes a memory)
  2. asks back about it (recalls via relevance memory snapshot)
  3. uses a tool

### Push: M3.h

`git push` updates Draft PR #84.

---

## Notes

- Each `*_pi.py` should REUSE pure logic bodies from its langgraph counterpart where possible (import private helpers, copy with attribution when not exportable). Don't redesign behavior.
- Middleware that depends on `ctx.extra` keys must initialize them safely (use `ctx.extra.setdefault(...)`).
- `Middleware` base from cubepi: subclass it; only implement the hooks needed; cubepi's `_has_method` machinery skips unimplemented hooks during composition.
- For each middleware that mutates the persisted state (memory snapshots, todo state, etc.), the snapshot/state update MUST be deterministic (cache discipline). No timestamps in stable prefix; no random ordering.

## Out of scope for M3

- Frontend changes (none needed — SSE shape stable)
- Auth / RBAC (orthogonal)
- M3 does NOT delete the langgraph middleware files yet; that's M6 cleanup
