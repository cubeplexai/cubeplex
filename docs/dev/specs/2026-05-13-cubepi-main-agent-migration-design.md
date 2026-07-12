# cubepi main agent migration — Design

Date: 2026-05-13
Status: Design
Branch: `feat/integrate-cubepi`
Companion spec: `~/cubepi/docs/specs/2026-05-13-cubepi-cubeplex-readiness-design.md` (Spec A)

## Why this spec exists

cubeplex's agent runtime is currently built on LangChain `create_agent`
+ LangGraph. We're replacing it entirely with cubepi (async-native
agent framework Python port of pi-agent-core, owned by the same
authors). The end state:

- `langchain`, `langgraph`, `langchain-anthropic`, `langchain-openai`,
  `langchain-mcp-adapters`, `langgraph-checkpoint-postgres` all
  removed from `pyproject.toml`
- All 11 middleware reimplemented against cubepi's middleware
  protocol
- Postgres checkpointer switched from langgraph's `AsyncPostgresSaver`
  to cubepi's `PostgresCheckpointer`
- All 6 builtin tools and the MCP runtime ported

This is a large migration. The decision to go in one shot (rather
than stage through a subagent-only spike) was deliberate: most
intermediate glue was throwaway, and cubepi self-tests (via
`CUBEPLEX_E2E_LLM_*` against a real LLM endpoint in Spec A's
acceptance) cover the early-validation value of a spike.

## Non-goals

- Backward compatibility with existing langgraph-format conversation
  data. cubeplex is not released; existing dev/staging conversations
  can be wiped.
- Backward compatibility with langgraph at the user-visible behavior
  level beyond what's documented (SSE event stream stays identical,
  REST API stays identical, frontend unchanged).
- Migrating to cubepi `OpenAIResponsesProvider` for OpenAI-official
  reasoning models. cubeplex's openai-compatible endpoints (DeepSeek
  etc.) don't support Responses API; we stay on Chat Completions
  for them. Adding `OpenAIResponsesProvider` path is a separate
  spec.
- Adding stdio MCP support to cubeplex. cubeplex only uses HTTP MCP
  servers. cubepi has stdio support (Spec A D2) but it's for future
  cubepi-coding-agent use cases.

## Dependencies on Spec A

cubeplex path-depends on `~/cubepi` throughout the migration. Spec A
deliverables (D1-D9) must land in cubepi before the corresponding
cubeplex milestone:

| Cubeplex milestone | Requires Spec A deliverables |
|---|---|
| M0 Foundation | D1 (PostgresCheckpointer), D5 (Message.metadata), D9 ([postgres] extra) |
| M1 Agent core | D3 (cache_policy), D5, D6 (AgentContext.extra), D8 (after_model_response) |
| M2 Tools layer | D2 (MCP HTTP), D9 ([mcp] extra) |
| M3 Middleware | D3, D5, D6, D7 (transform_system_prompt), D8 |
| M4 Services & ancillary | D3, D4 (OpenAIProvider OSS reasoning) |
| M5 Testing | none new |
| M6 Cleanup | all of above |

Path dependency means cubepi changes are available immediately;
formal cubepi release is not required.

## Approach: dual-track during migration, single-track at end

Each component is rewritten in a new file with `_pi` suffix
(e.g. `subagents_pi.py`, `cost_pi.py`). The langgraph-era file stays
in place. A runtime flag selects which path runs:

```yaml
# config.yaml additions
agents:
  runtime: langgraph    # or "cubepi"; CI sets cubepi; default flips to cubepi at M6
```

```python
# config.py
class AgentRuntimeConfig(BaseModel):
    runtime: Literal["langgraph", "cubepi"] = "langgraph"
```

Why dual-track:
- Failed cubepi-path PRs don't block other unrelated PRs that touch
  langgraph code
- Manual langgraph regression remains possible if cubepi path
  reveals a behavioral regression
- The flag is the rollback switch; we can flip to cubepi default
  ahead of cleanup, watch staging, flip back if needed

At M6 cleanup, the `_pi` files are renamed back to canonical names
and the langgraph-era code is deleted.

## State migration: cubepi's two-slot model

cubeplex's `CubeplexState` extends LangGraph's `AgentState` with three
channels:

| LangGraph state channel | cubepi destination | Rationale |
|---|---|---|
| `memory_snapshots: dict[str, dict]` | `UserMessage.metadata["memory_snapshot"]` on each user message | Snapshots are per-user-message immutable. Storing on the message they belong to leverages cubepi's append-only model — physical immutability replaces cubeplex's `_merge_snapshots` reducer. |
| `compaction: CompactionSummary` | `extra["compaction"]` | Singleton per-thread state. |
| `compaction_until_msg_index: int` | `extra["compaction_until_msg_index"]` | Same. |

`TodoListMiddleware`'s `PlanningState` channels (6 of them) all go
into `extra`:

- `extra["todos"]`
- `extra["todo_guard_retries"]`
- `extra["todo_guard_blocked"]`
- `extra["todo_guard_suppressed"]`
- `extra["todo_stale_iterations"]`
- `extra["todo_finalization_correction"]`

Why per-message metadata for memory_snapshots specifically: cubepi
messages are physically append-only (no UPDATE path on
`cubepi_messages` for `payload` or `metadata`). This gives the same
"cannot overwrite" invariant as cubeplex's current `_merge_snapshots`
reducer, but enforced at the storage layer rather than as Python
runtime check. Cache correctness becomes a structural property.

## Middleware → cubepi hook mapping

Single source of truth for which cubepi hooks each middleware uses:

| middleware | Agent.tools | transform_context | transform_system_prompt | before_tool_call | after_tool_call | after_model_response |
|---|---|---|---|---|---|---|
| Artifact | ✅ | ✅ | — | — | — | — |
| Attachment | — | ✅ | — | — | — | — |
| Cost | — | — | — | — | — | ✅ |
| Memory | — | ✅ | — | — | — | — |
| Sandbox | ✅ | — | ✅ | — | — | — |
| Skills | — | — | ✅ | — | — | — |
| SubAgent | ✅ | — | — | — | — | — |
| Timestamp | — | ✅ | — | ✅ | ✅ | ✅ |
| Todo | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Citation | — | — | — | — | ✅ | — |
| Compaction | — | ✅ | — | — | — | — |

`should_stop_after_turn` is NOT used by any cubeplex middleware.
`convert_to_llm` is NOT used. These cubepi hooks remain as cubepi
features; cubeplex just doesn't need them.

## Prompt cache discipline preservation

cubeplex's existing prompt cache discipline (documented in
`backend/CLAUDE.md`) must be preserved exactly. The migration must
not cause cache regression.

Three-tier validation:

### Tier 1 — Unit tests (every commit)

Ported from existing `tests/unit/test_cache_markers.py` and
`tests/unit/test_memory_cache_stability.py`. New:
`tests/unit/test_cubepi_conversion_stability.py` pinning:

- `cubepi.Message → Anthropic API dict` byte-stable across calls,
  matches checked-in fixture
- `cubepi.Message → OpenAI Chat Completions dict` byte-stable
- Tool definition serialization order deterministic
- `transform_system_prompt` chain output deterministic
- `Message.metadata` msgpack round-trip byte-identical
- cubeplex `CacheMarkerPolicy` walks back to last completed
  AIMessage correctly under various message-list shapes

### Tier 2 — E2E cache rate gate (real_llm marker)

`tests/e2e/memory/test_prompt_cache.py` ported (or re-run) under the
cubepi path. Same bar:

- Turn 2 cache_read ratio ≥ 50%
- Final turn (N=10) cache_read ratio ≥ 85%
- Gated by `CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true` env

CI runs this against the cubepi path. The langgraph path is run
manually as needed for regression comparison; not in CI.

### Tier 3 — Byte-equivalence test (new)

`tests/e2e/test_runtime_byte_parity.py`: a fixed conversation
scenario is replayed through both runtimes (langgraph and cubepi).
The outbound HTTP request bodies sent to the Anthropic / OpenAI API
are intercepted (via `respx` or `pytest-httpx`) and compared
field-by-field after `canonical_json` normalization.

The two paths must produce byte-identical API requests for the same
input scenario. Any divergence is a migration regression — typically
caused by:

- Different message conversion (extra `null` field, field reordering)
- Different cache marker placement
- Different system prompt assembly order
- Different tool definition format

This test is the **hard gate** for flipping the default flag from
langgraph to cubepi at M6.

## Milestone breakdown

The 6 milestones below are work units for the implementation plan,
which is produced separately via the writing-plans skill. Each
milestone is independently committable. Specific task ordering inside
each milestone is for the plan stage.

### M0 — Foundation

Depends on Spec A: D1, D5, D9.

| File | Action |
|---|---|
| `backend/pyproject.toml` | Add `cubepi[postgres,mcp]` via `[tool.uv.sources]` pointing to `/home/chris/cubepi` (editable). |
| `backend/alembic/env.py` | Import `cubepi.checkpointer.postgres.models.cubepi_metadata`; set `target_metadata = [SQLModel.metadata, cubepi_metadata]`. |
| `backend/alembic/versions/` | New revision: autogen creates `cubepi_threads`, `cubepi_messages` (with PARTITION BY clause), `cubepi_schema_version`. Migration manually calls `create_message_partitions_op()` and `write_schema_version_op()` from cubepi alembic helpers. |
| `backend/cubeplex/agents/checkpointer_pi.py` | New: thin wrapper around `cubepi.PostgresCheckpointer`, with cubeplex-side connection pool management. |
| `backend/cubeplex/llm/factory.py` | Add `build_cubepi_provider(provider_config) -> cubepi.Provider` routing by `api` field. Keep existing `build_langchain_model()` for langgraph path. |
| `backend/cubeplex/config.py` | Add `AgentRuntimeConfig.runtime` flag. |

### M1 — Agent core skeleton

Depends on M0, Spec A: D3, D5, D6, D8.

| File | Action |
|---|---|
| `backend/cubeplex/agents/graph_pi.py` | New: `create_cubeplex_cubepi_agent(...)` mirrors `create_cubeplex_agent` API surface, internally constructs `cubepi.Agent`. |
| `backend/cubeplex/agents/stream_pi.py` | New: `convert_cubepi_event_to_sse(...)` translation table. cubepi `thinking_*` → cubeplex `reasoning`; cubepi `toolcall_end` → cubeplex `tool_call`; cubepi `text_delta` / `error` / `done` pass through. |
| `backend/cubeplex/agents/convert_pi.py` | New: cubepi.Message ↔ API wire format. Handles `metadata` field. |
| `backend/cubeplex/agents/hydrator_pi.py` | New if needed: adapt `cubepi.CheckpointData` → API response shape (likely small delta). |
| `backend/cubeplex/llm/cache_markers_pi.py` | New: `CubeplexCacheMarkerPolicy(CacheMarkerPolicy)` walks back to last completed AIMessage; passed into `cubepi.AnthropicProvider(cache_policy=...)`. |
| `backend/cubeplex/api/v1/.../messages.py` | Dispatch on `config.agents.runtime` flag to either old or new agent factory. |

### M2 — Tools layer

Depends on M0, Spec A: D2.

| File | Action |
|---|---|
| `backend/cubeplex/tools/registry_pi.py` | New: registry over `cubepi.AgentTool`. |
| `backend/cubeplex/tools/builtin/calculator_pi.py` | Port to `AgentTool`. |
| `backend/cubeplex/tools/builtin/datetime_tool_pi.py` | Port. |
| `backend/cubeplex/tools/builtin/view_images_pi.py` | Port. |
| `backend/cubeplex/tools/builtin/memory_pi.py` | Port (writes memory tables — no cubepi state coupling). |
| `backend/cubeplex/tools/builtin/load_skill_pi.py` | Port (coordinates with `SkillsMiddleware` for system prompt injection). |
| `backend/cubeplex/mcp/runtime_pi.py` | Use `cubepi.mcp.load_mcp_tools_http()`. |
| `backend/cubeplex/mcp/discovery_pi.py` | Same. |

### M3 — Middleware migration (11 files)

Depends on M0, M1, Spec A: D3, D5, D6, D7, D8.

Five batches by complexity. Each middleware is a new `*_pi.py` file
implementing `cubepi.Middleware`. Unit tests ported in parallel.

**M3a — Simple (transform_context only)**

| File | Hook(s) used |
|---|---|
| `middleware/artifacts_pi.py` | `Agent.tools`, `transform_context` |
| `middleware/attachments_pi.py` | `transform_context` |
| `middleware/citation_pi.py` | `after_tool_call` |

**M3b — State-coupled**

| File | Hook(s) |
|---|---|
| `middleware/memory_pi.py` | `transform_context`; writes snapshot into `UserMessage.metadata` at the cubeplex layer (caller-side, when constructing the message before append) |
| `middleware/compaction_pi.py` | `transform_context`, reads/writes `ctx.extra["compaction"]` |

**M3c — System prompt / tool injection**

| File | Hook(s) |
|---|---|
| `middleware/sandbox_pi.py` | `Agent.tools`, `transform_system_prompt` |
| `middleware/skills_pi.py` | `transform_system_prompt` |
| `middleware/subagents_pi.py` | `Agent.tools` (the subagent tool's execute spawns another `cubepi.Agent`, much simpler than langgraph version — no bridging) |

**M3d — Telemetry**

| File | Hook(s) |
|---|---|
| `middleware/cost_pi.py` | `after_model_response` |
| `middleware/timestamps_pi.py` | `transform_context`, `before_tool_call`, `after_tool_call`, `after_model_response` |

**M3e — Complex (Todo)**

| File | Hook(s) |
|---|---|
| `middleware/todo_pi.py` | `Agent.tools`, `transform_system_prompt`, `transform_context`, `after_tool_call`, `after_model_response`, full `ctx.extra` usage |

TodoListMiddleware verification (every behavior of current
implementation maps cleanly):

| Current behavior | cubepi hook |
|---|---|
| Inject `write_todos` tool | `Agent.tools` |
| Capture `Command(update={"todos": ...})` from tool | `after_tool_call` intercepts write_todos, writes `ctx.extra["todos"]` |
| Append system prompt about write_todos | `transform_system_prompt` |
| Render current todos in model context | `transform_context` |
| `after_model` validate response (parallel calls, schema, stale) | `after_model_response` |
| Inject SystemMessage reminder | `TurnAction.inject_messages` |
| Force loop to model (`jump_to: "model"`) | `TurnAction(decision="loop_to_model")` |
| Force stop (`jump_to: "end"`) | `TurnAction(decision="stop")` |
| Update 6 PlanningState channels | `ctx.extra` direct mutation |

### M4 — Services & ancillary

Depends on M0, Spec A: D3, D4.

| File | Action |
|---|---|
| `backend/cubeplex/services/conversation_title.py` | Direct `cubepi.Provider` call; no agent loop. |
| `backend/cubeplex/services/provider_service.py` | Mostly metadata routing; likely no change. |
| `backend/cubeplex/streams/run_manager.py` | Adapt to cubepi event shape + new event queue protocol. |

### M5 — Testing (vertical, across M0-M4)

CI configuration:
- `CUBEPLEX_AGENTS__RUNTIME=cubepi` for all CI jobs
- Langgraph path retained for manual regression runs only

Test work:

| File | Action |
|---|---|
| `tests/unit/test_cache_markers.py` | Port for `CubeplexCacheMarkerPolicy` + cubepi providers. |
| `tests/unit/test_memory_cache_stability.py` | Port: snapshot rendering byte-stability tests against cubepi message flow. |
| `tests/unit/test_cubepi_conversion_stability.py` | New: §"Tier 1" tests above (fixture-anchored byte stability). |
| `tests/e2e/memory/test_prompt_cache.py` | Run unchanged under cubepi runtime (test is SSE-level, runtime-agnostic). |
| `tests/e2e/test_runtime_byte_parity.py` | New: Tier 3 hard gate. Intercepts outbound HTTP via `respx` / `pytest-httpx`; compares Anthropic API request bodies between langgraph and cubepi paths under fixed scenarios. |
| Existing E2E tests | Run unchanged under cubepi; any failure is a migration regression. |

### M6 — Cleanup

Depends on all M0-M5 stable in CI; Tier 3 byte-parity test green;
staging observation period satisfied.

| Action | Detail |
|---|---|
| Flip default flag | `config.development.yaml` / `config.production.yaml`: `agents.runtime: cubepi` |
| Rename `*_pi.py` files back to canonical names | Delete the langgraph version, rename `*_pi.py` → `*.py`. Applies to 11 middlewares + agent core + tool builtins + llm files. |
| Delete `cubeplex/agents/state.py` | `CubeplexState` no longer referenced. |
| Delete `cubeplex/llm/openai_compatible.py` | Replaced by `cubepi.OpenAIProvider`. |
| Delete `cubeplex/llm/cache_markers.py` | Replaced by `CubeplexCacheMarkerPolicy` + cubepi cache_policy mechanism. |
| Remove `AgentRuntimeConfig` flag | All references deleted. |
| Drop deps from `pyproject.toml` | `langchain`, `langchain-core`, `langchain-openai`, `langchain-mcp-adapters`, `langgraph`, `langgraph-checkpoint-postgres`, `langchain-anthropic`. Run `uv lock`. |
| Update `CLAUDE.md` | Replace "LangGraph backend" / "langchain.agents.create_agent" / state-channel terminology with cubepi equivalents. Update "Architecture" section. Update prompt cache discipline section to reference cubepi-side mechanisms. |
| Verify `make check` clean | format + lint + type-check + test all green. |

Existing langgraph checkpoint tables (`checkpoints`, `checkpoint_writes`,
etc. created at runtime by `AsyncPostgresSaver.setup()`) are left
orphaned. They're managed by langgraph at runtime, not cubeplex
alembic. With langgraph deps removed, no new instance will create
them. Existing dev databases can be wiped (no released data).

## Acceptance criteria (Spec B)

| # | Check | Gate |
|---|---|---|
| B-A1 | All Tier 1 unit tests pass under cubepi path | Every PR |
| B-A2 | All existing E2E tests pass under cubepi path | Every PR |
| B-A3 | `tests/e2e/memory/test_prompt_cache.py` cubepi path: turn 2 cache_read ≥ 50%, final ≥ 85% (with `CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true`) | Every PR touching cubepi path |
| B-A4 | `tests/e2e/test_runtime_byte_parity.py` green: langgraph and cubepi paths produce byte-identical Anthropic API request bodies for fixed scenarios | Required before M6 (flag flip) |
| B-A5 | Staging observation period: cubepi-default flag active in non-CI environment for agreed duration with no regressions | Required before M6 |
| B-A6 | `pyproject.toml` contains zero `langchain*` / `langgraph*` deps; `uv lock` reflects this | Required to close M6 |
| B-A7 | `make check` clean post-cleanup | Required to close M6 |
| B-A8 | `CLAUDE.md` updated, no stale langgraph references | Required to close M6 |

## Trade-offs accepted

| # | Trade-off | Mitigation |
|---|---|---|
| T1 | File count doubles during dual-track period (`*_pi.py` + original) | M6 single-step cleanup; both tracked by linting/type-checking. |
| T2 | CI runs only cubepi path; langgraph may silently rot | Acceptable. Langgraph is the rollback safety net, not the production path. Manual smoke tests as needed. |
| T3 | 11 middleware ports are refactors, not rewrites — old quirks may carry over | Tier 3 byte-parity test catches actual API request divergence; per-middleware review during PR. |
| T4 | Tier 3 byte-parity test requires HTTP interception (`respx`/`pytest-httpx`) — non-trivial fixture work | One-time engineering investment, high leverage. |
| T5 | New cubepi hooks (`after_model_response`, `transform_system_prompt`, `TurnAction`, `ctx.extra`) are designed for cubeplex's needs | Designed as general-purpose API; not cubeplex-coupled in form. Documented in cubepi for any user. |
| T6 | No data migration from langgraph state to cubepi | Accepted (no released data). Dev environments wiped during transition. |
| T7 | Staging observation period not quantified | Determined at plan stage / before M6 based on actual risk profile. |
| T8 | Spec A and Spec B are not perfectly parallel — M0 depends on D1 / D5 | Accepted. Mid-stream M milestones can start as A items land; path-dep makes integration immediate. |

## Out of scope (deferred)

- cubepi `OpenAIResponsesProvider` integration into cubeplex (separate spec when first OpenAI-official reasoning endpoint is integrated)
- stdio MCP support in cubeplex (separate spec; future cubepi-coding-agent CLI use case)
- Auto-archival / pruning of old conversations
- `cubepi_messages` partitioning beyond 64 hash partitions (out of need for foreseeable scale)
- Time-travel / fork conversation features (cubepi protocol doesn't expose; schema is ready)
- Subagent state persistence across requests (cubepi subagents remain ephemeral)
- Tool call timing telemetry (`tool_call_started_at_by_index` etc) — host-side instrumentation handled via `before_tool_call` / `after_tool_call` hooks during port, not via provider quirks
- Frontend changes (SSE wire protocol unchanged)
- Auth / RBAC / workspace model changes (orthogonal)
- Provider configuration UI / Vault flow changes (orthogonal)
- LangChain retry / rate-limit equivalent in cubepi (Spec A out-of-scope; cubeplex can wrap if needed at LLMFactory)

## Open questions for plan stage

These are intentionally deferred to writing-plans:

- Specific ordering inside each milestone (the 11 middleware migrations
  in M3 can be parallelized in subagents, but the order matters for
  dependencies — e.g. Memory before Compaction since both read extra)
- Whether `services/conversation_title.py` migration is M0-early (it's
  a trivial standalone consumer of LLMFactory) or M4 (with other
  services)
- Tier 3 byte-parity scenario design — which fixtures, how many
  scenarios, what message-shape coverage
- Staging observation duration (B-A5)
