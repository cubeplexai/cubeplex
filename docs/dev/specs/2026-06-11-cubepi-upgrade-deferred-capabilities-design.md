# cubepi upgrade + capability tools → deferred groups

**Status:** draft
**Branch:** `feat/cubepi-upgrade-deferred`
**Author:** xfgong
**Date:** 2026-06-11

## Context

cubebox currently pins cubepi at `c25946e` (released as 0.10.0). Since that pin,
cubepi shipped 23 commits, headlined by a new **dispatch strategy** for
`DeferredToolGroup` that delivers tool schemas via `load_tools` tool results
instead of injecting them into the model-visible tools array. This keeps the
tools array and system prompt byte-stable across expansions — group expansion
no longer invalidates the prompt cache.

cubebox already uses `DeferredToolGroup` for one thing: per-server grouping of
MCP tools (`cubebox/mcp/disclosure.py`, gated by `progressive_disclosure.*`
config). Everything else (calculator, datetime, memory, view_images,
show_widget, generate_image, load_skill, scheduled_tasks, skills) is loaded
eagerly.

Separately, cubebox has its own bespoke "umbrella tool + operation
discriminator" pattern at `cubebox/agents/actions/builder.py`. Each
`AgentCapability` (scheduled_tasks: 8 ops; skills: 4 ops) is collapsed into one
`AgentTool` whose input is a discriminated union over operations. The umbrella
exists because cubepi 0.10's `inject` strategy invalidated the prompt cache
every time tools were added — so eager-load-many-tools was the only safe
option, and umbrella+operation was the way to pack many ops into one cache-stable
schema.

dispatch strategy makes that workaround unnecessary. A `DeferredToolGroup` with
per-operation `AgentTool`s gives the same token efficiency (catalog instead of
full schemas) **plus** native tool-calling semantics: `before_tool_call`,
tracing, widgets, and audit logs see the real operation name instead of
`scheduled_tasks(operation="create")`.

## Goals

1. Bump cubepi pin from `c25946e` to current `main` (HEAD `088fa66` at time of
   writing).
2. Verify MCP tools still work under the new default `dispatch` strategy —
   citation wiring, sandbox middleware, artifact middleware, and tracing all
   see the real tool name through the deferred dispatcher.
3. Replace the umbrella+operation pattern for `scheduled_tasks` and `skills`
   capabilities with per-operation `AgentTool`s grouped under
   `DeferredToolGroup`s. Delete `cubebox/agents/actions/builder.py`'s union /
   discriminator machinery when no callers remain.

## Non-goals

- **load_skill / find_skills do NOT migrate.** They are not tool-schema
  delivery; they load SKILL.md *markdown content* that `SkillsMiddleware`
  splices into the next system prompt. cubepi's `load_tools` returns
  `{name, description, parameters}` JSON — wrong shape for prose. The skills
  catalog in the system prompt + `load_skill` tool is its own progressive
  disclosure system and stays as-is.
- **generate_image stays eager.** Single-tool deferred groups have poor
  cost/benefit — the catalog line plus the `load_tools` round-trip cancel most
  of the schema savings on the rare runs where it would help. Revisit when a
  second sandbox-image tool appears (video / edit / audio) and a `cubebox:media`
  group becomes natural.
- **calculator / datetime / write_todos / subagent / memory_* stay eager.**
  Used on most turns; deferring just adds a round-trip with no token win.
- **view_images / show_widget stay eager.** Small schemas, deferring saves
  almost nothing.
- This change does not introduce expanded-group cross-run replay
  (`prepare_resumed_state`). cubebox doesn't restore deferred-group state on
  resume today (only MCP runs hit it, and MCP groups are re-built fresh each
  run). Out of scope for this work.

## Phase 1 — pin bump + MCP verification

Bump `backend/pyproject.toml` cubepi rev to current `main`. `uv lock` rewrites
`backend/uv.lock`.

Breaking changes to absorb:

| Change | cubebox impact |
|---|---|
| `deferred_tool_strategy` default `inject` → `dispatch` | MCP groups switch behavior. Tool calls now flow through `deferred_tool_call(tool_name, arguments)` and cubepi unwraps before the middleware pipeline. |
| `resumed_schemas` removed; `prepare_resumed_state(strategy=)` required | Not used in cubebox — no edits needed. |
| `inject` mode no longer renders schemas into system prompt | Not used in cubebox — no edits needed. |

Verification matrix (E2E, not unit):

1. **MCP tool name in `before_tool_call`** — add a temporary log line to
   `_compose.py`, run an MCP-enabled conversation, confirm the hook receives
   the real namespaced tool name (e.g. `github_create_issue`), not
   `deferred_tool_call`.
2. **Citation wiring** — `_deferred_citations` is populated when loader runs.
   Confirm citation middleware finds its config under the real tool name.
3. **Tracing** — `cubepi trace view` on a run that expanded a group should
   show the real tool name in the tool-call span, not the dispatcher
   envelope.
4. **Frontend widgets** — `toolcall_start` / `toolcall_end` events streamed to
   the frontend carry the real tool name (frontend tool-call widget logic
   does name-based dispatch).

If any verification fails, the issue is in cubepi `_dispatch_tool.py` or
`middleware.py`; fix upstream in `~/cubepi`, push, bump the pin again.

## Phase 2 — capability migration

Two capabilities to migrate, in this order (skills depends on per-run deps so
it's the harder one):

### 2a. scheduled_tasks

Current: `SCHEDULED_TASKS_CAPABILITY` (8 ops: list, get, create, update,
delete, pause, resume, get_run_history) → 1 umbrella `AgentTool` named
`scheduled_tasks`.

Target: 8 `AgentTool`s named after each op (`scheduled_tasks_list`,
`scheduled_tasks_create`, etc., or just `list_scheduled_tasks` —
naming-bikeshed decision in implementation), grouped as:

```python
DeferredToolGroup(
    group_id="cubebox:scheduled_tasks",
    display_name="Scheduled tasks",
    description="Create, list, update, pause, resume, and delete scheduled agent tasks.",
    tool_names=[...],  # the 8 op names
    loader=...,        # zero-arg async → list[AgentTool] with deps closed over
)
```

Mutation gating (`allow_mutations=False` for automated runs) moves from
`build_capability_tool` into the loader — the loader returns only ops that
survive the gate. Read-only runs see 3 tools (list, get, get_run_history);
interactive runs see 8.

### 2b. skills capability

Current: dynamically built per-run via `build_skills_capability(deps)` because
handlers close over `catalog`, `registry`, `session`, etc.

Target: same per-run construction, but the resulting tools become a
`DeferredToolGroup` with `group_id="cubebox:platform_skills"`. Loader closes
over `SkillDeps` the same way today's `_skills_cap` closes over them.

### 2c. delete the umbrella machinery

When no callers remain:

- `cubebox/agents/actions/builder.py` — delete `_build_union_model`,
  `_build_operation_model`, `_make_literal_type`. `build_capability_tool` either
  goes away entirely or shrinks to a thin "build per-op AgentTools from an
  AgentCapability" helper.
- `cubebox/agents/actions/types.py` — `AgentCapability` / `AgentOperation`
  either stay as a convenient declaration shape (and we add a `to_tools()`
  helper) or get inlined per-capability. Decision deferred to implementation.

## Risks

- **MCP middleware compatibility** — dispatch mode is supposed to be
  middleware-transparent, but cubebox has 11 middleware including bespoke
  citation / artifact / sandbox layers. Worst case: cubepi needs a fix.
  Caught by Phase 1 verification before any Phase 2 work begins.
- **Tool name collisions** — splitting `scheduled_tasks` into 8 tools risks
  colliding with other tool names. Resolve by prefix:
  `scheduled_tasks_list` etc.
- **Cache-prefix discipline** — the existing memory `loaded_skills`-style
  cache-prefix order documented in `run_manager.py:2094-2096` assumes a
  specific eager order. With more tools moving to deferred groups, the eager
  prefix shrinks, which is strictly good for cache stability — no regression
  expected, but spot-check with a trace.

## Out of scope (explicit)

- generate_image deferral (see Non-goals)
- load_skill / find_skills migration (see Non-goals)
- expanded-group cross-run replay
- frontend-side changes to tool-call widget rendering (real tool names already
  flow through the existing widget path)

## Naming

Per-op tool names use the **group-prefix form**: `<group>_<op>`. Examples:
`scheduled_tasks_create`, `scheduled_tasks_list`, `scheduled_tasks_pause`,
`scheduled_tasks_get_run_history`, `platform_skills_find`,
`platform_skills_install`.

Rationale:

1. Matches cubebox's existing MCP namespacing
   (`cubebox/mcp/cubepi_runtime.py:_build_namespaced_name_with_prefix` produces
   `<server_slug>_<tool>` e.g. `github_create_issue`). The deferred catalog
   then lists cubebox-side and MCP-side groups in the same shape.
2. Trace / log queries that currently match `scheduled_tasks*` keep working.
3. Sidesteps singular/plural inconsistency
   (`create_scheduled_task` vs `list_scheduled_tasks`).

Anthropic / OpenAI tool-name limit is 64 chars; longest projected name
(`scheduled_tasks_get_run_history`, 31 chars) is well within.

## Rollout

No gray-launch / dispatch-vs-inject side-by-side. Dispatch is a strict cache-hit
improvement and a pin revert is the rollback path if something regresses.
