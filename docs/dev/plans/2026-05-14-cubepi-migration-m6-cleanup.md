# cubepi Migration M6 — Cleanup Implementation Plan

> **Follow-up (2026-05-14):** M6.5 and M6.7 were finished in `docs/superpowers/plans/2026-05-14-cubepi-cleanup-followup.md`. The M6.7 commit landed in this plan only dropped the umbrella `langchain` / `langgraph` packages; the sub-packages (`langchain-core`, `langchain-openai`, `langchain-anthropic`, `langchain-mcp-adapters`) were dropped in the follow-up. M6.5 also left `cubeplex/mcp/runtime.py` and `discovery.py` in place; the follow-up replaced them with `cubepi_admin_discovery.py` / `cubepi_admin_refresh.py` and migrated all admin/OAuth callers.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Status: BLOCKED on M5.3 (prompt cache hit). Do NOT execute until cache gate cleared.**
See `docs/superpowers/notes/2026-05-14-cubepi-cache-miss-investigation.md`.

**Goal:** Remove the langgraph fallback path completely. After M6:
- `config.agents.runtime` flag deleted (no more dual-track)
- All `*_pi.py` files renamed to canonical names; original langgraph versions deleted
- `langchain*` / `langgraph*` dependencies removed from `pyproject.toml`
- `backend/CLAUDE.md` updated to reference cubepi instead of LangGraph

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M6.

## Pre-flight requirements (all MUST be satisfied)

| Gate | How to verify |
|---|---|
| M5.3 cache test passes under `agents.runtime=cubepi` | `CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true uv run pytest tests/e2e/memory/test_prompt_cache.py -m real_llm` → PASS |
| Tier 3 byte-parity tests pass (or accepted as not-required if M5.3 passes via different mechanism) | `uv run pytest tests/e2e/test_runtime_byte_parity.py --runxfail` → no FAILED |
| Staging observation period (recommended ≥ 1 week) under default `runtime=cubepi` | Manual sign-off |
| All existing E2Es pass under cubepi | Spec B § "Acceptance criteria" B-A1, B-A2 |

If any gate not met → defer M6.

## Tasks

### M6.0: Flip default config flag

- `backend/config.development.yaml`: `agents.runtime: "cubepi"` (was `langgraph`)
- `backend/config.production.yaml` (if exists): same
- `backend/config.test.yaml`: already `cubepi` (M0.4); leave as is
- Smoke: dev server starts, basic conversation works through cubepi

Commit: `feat(config): default agents.runtime to cubepi (M6.0)`

### M6.1: Rename middleware files (11 files)

Delete the langgraph `*.py`, rename `*_pi.py` to canonical name.

For each pair:
1. `git rm cubeplex/middleware/<name>.py`
2. `git mv cubeplex/middleware/<name>_pi.py cubeplex/middleware/<name>.py`
3. Update class name inside (drop `Pi` suffix): `class FooMiddlewarePi(Middleware)` → `class FooMiddleware(Middleware)`
4. Update all imports across codebase (run_manager, tests, etc.) to use new path

Affected:
- artifacts → artifacts
- attachments → attachments
- citation → citation (preserve citations/ package if it exists; the package layout for citation may differ)
- memory → memory
- compaction → compaction
- sandbox → sandbox
- skills → skills
- subagents → subagents
- cost → cost
- timestamps → timestamps
- todo → todo

Also rename test files: `tests/unit/test_<name>_pi.py` → `tests/unit/test_<name>.py`. Avoid clashing with existing tests for the langgraph version (delete them first since the langgraph version is gone).

Each middleware = 1 commit.

### M6.2: Rename agent core files

- `cubeplex/agents/graph.py` (langgraph) → DELETE
- `cubeplex/agents/graph_pi.py` → `cubeplex/agents/graph.py` + rename `create_cubeplex_cubepi_agent` → `create_cubeplex_agent`
- `cubeplex/agents/stream.py` (langgraph) → DELETE
- `cubeplex/agents/stream_pi.py` → `cubeplex/agents/stream.py` + rename function names
- `cubeplex/agents/convert.py` (langgraph) → DELETE
- `cubeplex/agents/convert_pi.py` → `cubeplex/agents/convert.py`
- `cubeplex/agents/checkpointer.py` (langgraph wrapping AsyncPostgresSaver) → DELETE
- `cubeplex/agents/checkpointer_pi.py` → `cubeplex/agents/checkpointer.py`
- `cubeplex/agents/state.py` (CubeplexState) → DELETE entirely (not replaced)

Update all imports + tests.

Commit: `refactor(agents): delete langgraph variants; rename _pi to canonical`

### M6.3: Rename LLM files

- `cubeplex/llm/openai_compatible.py` (ChatOpenAICompatible) → DELETE
- `cubeplex/llm/cache_markers.py` (apply_cache_markers, _wrap_with_cache_markers) → DELETE
- `cubeplex/llm/cache_markers_pi.py` → `cubeplex/llm/cache_markers.py` (or merge into factory.py if logic is small)
- `cubeplex/llm/factory.py` — remove `build_langchain_model()` / `create_default()` / `_wrap_with_cache_markers` / `_create_langchain_client` etc. Keep only the cubepi-related methods. Rename `build_cubepi_provider()` to `build_provider()` if cleaner.

Commit: `refactor(llm): delete langchain client code paths`

### M6.4: Rename tool builtins (5 files)

- `cubeplex/tools/builtin/calculator.py` → DELETE
- `cubeplex/tools/builtin/calculator_pi.py` → `cubeplex/tools/builtin/calculator.py`
- `cubeplex/tools/builtin/datetime_tool.py` → DELETE
- `cubeplex/tools/builtin/datetime_tool_pi.py` → `cubeplex/tools/builtin/datetime_tool.py`
- `cubeplex/tools/builtin/view_images.py` → DELETE
- `cubeplex/tools/builtin/view_images_pi.py` → `cubeplex/tools/builtin/view_images.py`
- `cubeplex/tools/builtin/memory.py` → DELETE
- `cubeplex/tools/builtin/memory_pi.py` → `cubeplex/tools/builtin/memory.py`
- `cubeplex/tools/builtin/load_skill.py` → DELETE
- `cubeplex/tools/builtin/load_skill_pi.py` → `cubeplex/tools/builtin/load_skill.py`
- `cubeplex/tools/registry.py` → DELETE
- `cubeplex/tools/registry_pi.py` → `cubeplex/tools/registry.py`

Update imports + tests.

Commit: `refactor(tools): delete langchain BaseTool versions; rename _pi to canonical`

### M6.5: Rename MCP files

- `cubeplex/mcp/runtime.py` (uses langchain-mcp-adapters + OAuthTokenManager wiring for langgraph) → DELETE or PRESERVE
- `cubeplex/mcp/discovery.py` (langchain-mcp-adapters-based discovery) → DELETE
- `cubeplex/mcp/runtime_pi.py` → `cubeplex/mcp/runtime.py`
- `cubeplex/mcp/discovery_pi.py` → `cubeplex/mcp/discovery.py`

NOTE: OAuthTokenManager integration was deferred to cubepi (M2.4 known issue). If OAuth-MCP is still required, port the OAuth path now before deleting the langgraph runtime. Otherwise drop OAuth-MCP from supported feature set; document.

Commit: `refactor(mcp): delete langchain-mcp-adapters paths; rename _pi`

### M6.6: Remove AgentRuntimeConfig flag

- `cubeplex/config.py`: delete `AgentRuntimeConfig` class + field on root Config
- `backend/config.*.yaml`: remove `agents:` block
- `backend/cubeplex/streams/run_manager.py`: delete the `if runtime == "cubepi"` dispatch in `_execute_run`; cubepi path becomes the only path; rename `_run_cubepi_path` → `_run` (or fold into `_execute_run` directly)
- `backend/cubeplex/api/routes/v1/conversations.py`: delete the dispatch in `_get_history_messages`; merge into a single function
- `backend/cubeplex/services/conversation_title.py`: delete `_generate_title_langgraph`; keep only `_generate_title_cubepi` (rename if cleaner)

Commit: `refactor: remove agents.runtime dispatch flag; cubepi is the only path`

### M6.7: Drop langgraph/langchain dependencies

In `backend/pyproject.toml`, remove from `dependencies`:
- `langchain`
- `langchain-core`
- `langchain-openai`
- `langchain-anthropic`
- `langchain-mcp-adapters`
- `langgraph`
- `langgraph-checkpoint-postgres`

Run `uv lock` to regenerate lock file (will drop transitive deps too).

Smoke: `uv run python -c "import cubeplex"` succeeds; no `langchain.*` / `langgraph.*` imports anywhere.

Commit: `chore(deps): drop langchain + langgraph dependencies`

### M6.8: Update CLAUDE.md

In `backend/CLAUDE.md`:
- Replace "LangGraph" / "LangChain" terminology with "cubepi"
- Architecture section: describe cubepi-based agent + AgentEvent + Middleware
- Prompt Cache Discipline section: reference cubepi cache_control via CacheMarkerPolicy, ctx.extra-based state, message metadata for snapshots
- Remove "LangSmith" config var (if not used by cubepi)
- Remove "state_schema=CubeplexState" examples
- Remove references to `langchain.agents.create_agent()`

In root `CLAUDE.md` (if exists): same kind of update.

Commit: `docs(claude-md): update for cubepi runtime (M6.8)`

### M6.9: Final verification + push

- `cd backend && make check` — format + lint + type-check + test → green
- All unit + E2E tests pass
- Smoke: `python main.py` starts, conversation works
- Tag branch: `git tag cubepi-migration-complete`
- `git push --tags`

Mark PR #84 ready-for-review (out of draft).

## Rollback strategy

If issues surface after M6 commits land:
- Each M6.* commit is small + atomic; revert specific commits
- If catastrophic, revert the entire branch back to pre-M6 (last stable point: this plan's commit + the M5 final state with both runtimes coexisting)
- The cubepi codebase at /home/chris/cubepi remains as the new dependency surface — rollback doesn't undo cubepi's improvements

## Out of scope

- Cubepi v0.3.0 PyPI release (separate cubepi-repo concern; M6 keeps path dep)
- Further cubepi feature additions
- Performance regression testing

## Estimated effort

If pre-flight gates are met:
- ~6 hours of focused work
- ~15-25 commits
- All atomic + revertible
