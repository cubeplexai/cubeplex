# MCP Progressive Disclosure — Design

Date: 2026-05-27
Branch: `feat/mcp-progressive-disclosure`
Worktree slot: 89
Issue: #143

## Problem & motivation

Today every enabled MCP server's full tool set — name, description, and the complete JSON
input schema for each tool — is injected into the agent's tool list on every run. With one or
two small servers this is fine. As a workspace connects more servers (a CRM connector, a
ticketing connector, a docs connector, a code-host connector…), the combined schema payload
grows quickly:

- **Large context.** Tool definitions are part of the cached prefix sent to the model on every
  turn. A handful of rich servers can push the tool block into the tens of thousands of tokens
  before the user has typed anything.
- **High cache cost.** That block is large and re-billed at cache-write rate whenever the prefix
  changes, and at cache-read rate every turn. More servers = a bigger fixed tax per turn.
- **Diluted attention / worse tool selection.** Published benchmarks show tool-selection accuracy
  drops as the toolset grows; the model has to scan dozens of near-identical schemas to pick one.

cubeplex already solved the same shape of problem for **skills**: by default the system prompt
carries only a compact "Available skills" index (name + one-line description), and the model
calls `load_skill(name)` to pull the full instructions into context on demand. This spec applies
the same idea to MCP: expose a compact catalog of connected servers by default, and expand a
given server's full tool set only when the model asks.

## Goals

- By default, inject only a **compact MCP catalog** into the prompt: per server, a name, a
  one-line description, and short trigger hints — not the per-tool JSON schemas.
- Let the model **expand a server on demand** so its full tool set + schemas become available for
  the rest of the conversation.
- **Preserve the prompt-cache prefix.** The catalog (and any expanded set) must be byte-stable
  across turns of the same conversation; this is the hard constraint from
  `backend/docs/prompt-cache-discipline.md`.
- Keep MCP auth, citations, and tool namespacing working exactly as today once a server is
  expanded — progressive disclosure is purely about *when* schemas enter context, not *how* tools
  run.
- Make the behavior **configurable / opt-in** so small workspaces keep today's zero-indirection
  behavior.

## Non-goals

- No change to MCP auth (OAuth/static/none), credential vault, or the four-layer install model.
- No change to how a tool actually executes once exposed (transport, namespacing, citations).
- No semantic / embedding-based tool retrieval in v1 (noted as a later option below).
- No "code mode" (exposing tools as a callable API the model writes code against) in v1.
- No frontend redesign of MCP management surfaces. A small read-only indicator of which groups
  expanded during a run is acceptable but not required for v1.
- No per-tool (sub-group) disclosure in v1 — the unit of expansion is a whole group (= MCP server
  in v1).
- No non-MCP group types in v1 — the cubepi primitive is generic, but cubeplex v1 only maps MCP
  servers to groups.

## Current state in cubeplex (how MCP tools reach the prompt today)

The flow, end to end:

1. **Discovery (already cached in DB).** When a connector is installed/refreshed, its tool list is
   discovered and stored on `MCPConnectorInstall.tools_cache`
   (`backend/cubeplex/models/mcp.py`, a `list[dict]` of tool definitions including
   `input_schema`). Server/handshake metadata lives in `discovery_metadata`. Per-tool citation
   config lives in `tool_citations`. So **we already have each server's tool schemas in Postgres
   without doing live discovery at prompt-assembly time** — important for building an index cheaply.

2. **Per-run load.** `RunManager._run_cubepi_path` (`backend/cubeplex/streams/run_manager.py`,
   ~line 1039) calls `load_workspace_mcp_tools_for_cubepi`
   (`backend/cubeplex/mcp/cubepi_runtime.py`). That function:
   - asks `MCPEffectiveConnectorService.list_runtime_specs(...)` for one
     `MCPRuntimeConnectorSpec` per *usable* install (`backend/cubeplex/mcp/effective.py`, line 305),
   - resolves auth headers per server,
   - calls `cubepi.mcp.load_mcp_tools_http(...)` to fetch the live tool list,
   - namespaces each tool as `{slug}__{tool_name}` (length-capped at 64), and
   - returns `(list[AgentTool], dict[str, CitationConfig])`.

3. **Tool assembly (cache-ordered).** Those MCP tools are appended **last** in a deliberately
   fixed order (`run_manager.py` ~line 900 comment block): sandbox → artifact → todo → subagent →
   calculator/datetime → view_images → generate_image → memory_* → load_skill → **mcp_tools**. The
   merged `all_tools` list becomes the agent's tool definitions (`tools=all_tools`, ~line 1389).
   This whole tool block is part of the cache-eligible prefix; the cache discipline doc explicitly
   calls out "Tool definitions in deterministic order" as part of the stable prefix and notes
   "Toggling MCP tools mid-conversation is treated as a new conversation."

4. **System-prompt assembly.** `BASE_SYSTEM_PROMPT` + optional per-workspace `AgentConfig` prompt
   + (if any skills enabled) the **skills index** rendered from `SKILLS_PROMPT_TEMPLATE`
   (`backend/cubeplex/prompts/skills.py`) — a sorted bullet list of `` `name` — description ``. This
   index is appended as a *stable suffix* of the system prompt so it stays cache-safe.

### The skills precedent (the analog to copy)

- **Index in the prompt.** `run_manager.py` ~line 1799 fetches enabled skills and appends a sorted
  bullet list via `SKILLS_PROMPT_TEMPLATE`. Sorting is what keeps it byte-identical across turns.
- **`load_skill` tool.** `backend/cubeplex/tools/builtin/load_skill.py` returns the SKILL.md content
  as a JSON tool result (`LoadSkillOutput`).
- **`SkillsMiddleware`.** `backend/cubeplex/middleware/skills.py` watches `after_tool_call` for
  `load_skill`, stashes loaded content into `agent._extra["loaded_skills"]` (via an `extra_ref`
  closure), and on every subsequent model call appends each loaded skill's body to the system
  prompt in `transform_system_prompt` — **sorted by name** for determinism.

The key cache insight from skills: expanded content is appended to the **system prompt suffix**
(after the base prompt, deterministically ordered), so each expansion is a stable, monotonic,
append-only growth of the prefix. Within a turn the prefix is fixed; across turns it only ever
grows by appending — never reorders — so earlier cache segments stay valid.

## Industry research (with citations)

The "load a compact index, expand on demand" pattern is now the mainstream answer to tool/context
bloat. Three families of prior art, from provider-side features to self-hosted agent runtimes.
What's transferable to cubeplex:

- **Anthropic Tool Search Tool / deferred tools (GA Feb 2026).** You register all tools but mark
  most with `defer_loading: true`; only a search tool plus a few always-on tools are in context.
  The model searches (regex or BM25) and the API returns 3–5 `tool_reference` blocks that expand
  into full definitions. Reported ~85% token reduction (e.g. ~77K → ~8.7K for 50+ MCP tools) and
  large accuracy gains on big tool libraries (Opus 4.5 79.5% → 88.1%). Scales to ~10k tools.
  *Transferable:* validates the index-then-expand shape and confirms expansion should pull only a
  small relevant subset, not everything. *Caveat for us:* this is a provider-side feature on the
  Anthropic API; cubeplex runs through cubepi's provider abstraction and multiple providers
  (OpenAI-compatible, deepseek), so we cannot depend on it being present everywhere. Our
  host-level mechanism must work provider-agnostically.

- **MCP host-side filtering / progressive disclosure (general guidance).** Multiple write-ups note
  the host need not forward every discovered tool to the model; it can filter, search, or disclose
  progressively before anything hits context. Standard MCP setups can eat up to ~72% of the
  context window on definitions alone, with tool-selection accuracy dropping as the set grows.
  *Transferable:* cubeplex **is** the host here (`RunManager` assembles the tool list), so we own
  this lever directly — exactly where the skills index already sits.

- **Cloudflare "Code Mode" (search + execute, ~1,000 tokens for 2,500+ endpoints).** Tools are
  presented as an API the model writes code against; a `search()` tool queries the spec (which
  never enters context) and `execute()` runs the call. ~99.9% token reduction at extreme scale.
  *Transferable as a later option:* the radical end of the spectrum; overkill for v1 but worth
  noting for very large connector counts.

- **Anthropic "tools as a filesystem" / RAG-over-tools.** Present tools as something the model
  explores incrementally; or retrieve relevant tools by embedding similarity instead of a flat
  list. *Transferable as a later option:* semantic retrieval is the natural upgrade if a
  name+description catalog proves too coarse at high server counts.

- **hermes-agent Tool Search (production, `~/hermes-agent/tools/tool_search.py`).** A full
  progressive-disclosure layer for an agent runtime with MCP + plugin tools. Three bridge tools
  (`tool_search`, `tool_describe`, `tool_call`) replace deferred tool schemas. Core tools
  (`_HERMES_CORE_TOOLS`) never defer. Key design points:
  - **Granularity = single tool** (vs. our server/group-level).
  - **Threshold = context-window percentage** (default 10%): activates when deferrable tool schemas
    would consume ≥ N% of context, not when a server-count threshold is crossed. This is more
    direct — 3 tiny servers don't need deferral, 1 giant server does.
  - **Stateless catalog**: rebuilt from the current tool-defs list every assembly, never cached
    across turns. Lesson from OpenClaw #84141: a session-keyed catalog that drifts from the live
    registry silently drops tools. cubeplex avoids the same drift differently (live loader on expand,
    not cached-schema synthesis), but the failure mode is worth defending against.
  - **BM25 retrieval** over tokenized tool name + description + parameter names, with substring
    fallback. Needed because their catalog is hundreds of individual tools with no readable index.
  - **Toolset scoping**: bridge tools only see/invoke tools the session was granted. Defense in depth
    via `scoped_deferrable_names()` gate before dispatch.
  - **Transparent unwrap**: `tool_call` recurses into the real dispatcher; hooks/guardrails/activity
    feed see the underlying tool, not the bridge.
  *Transferable:* context-window-percentage threshold (adopted below), catalog-drift defense,
  transparent unwrap principle. *Not transferable:* per-tool granularity (too many round trips at
  our scale), stateless rebuild (conflicts with prompt-cache append-only invariant), BM25 (our
  catalog is small enough for the model to read directly).

- **LangChain / LangGraph dynamic tool management (2025–2026).** Three mechanisms:
  (1) LangGraph **dynamic tool calling** (Aug 2025) — graph-state-driven per-node tool-set
  switching; (2) LangChain 1.0 **`LLMToolSelectorMiddleware`** — secondary LLM call selects
  relevant tools per turn; (3) DIY **vector-store tool retrieval** — embed tool descriptions,
  top-k by similarity. All are per-turn automatic selection, not model-initiated expansion.
  *Transferable:* validates the "filter before the model sees" principle. *Not transferable:*
  per-turn LLM or vector retrieval adds latency/cost we avoid with a readable catalog.

Recommendation drawn from the research: build a **host-side, provider-agnostic** index-then-expand
mechanism in **cubepi** (the agent runtime) as a `DeferredToolGroup` primitive, with cubeplex
providing the MCP-specific mapping. Use context-window-percentage thresholds (hermes-agent's
approach). Keep semantic retrieval and code-mode as documented later-stage options.

## Proposed design

### Shape: deferred tool groups, with MCP servers as the v1 group type

Default behavior becomes: the prompt carries a **compact catalog** of collapsed tool groups; the
model calls a builtin tool to **expand** a named group; expanded groups' full tools + schemas
become available for the rest of the run.

The disclosure unit is a **tool group**, not an individual tool. MCP servers are the first (and v1
only) group type, but the underlying mechanism is group-agnostic so future group types (plugins,
large builtin suites) require no runtime changes — only a new mapping from source → group. This
matches how users think about MCP ("I connected Linear"), keeps the catalog short, and reuses the
existing per-server namespacing/citation plumbing wholesale.

### 0. Layering: cubepi primitive + cubeplex mapping

The core mechanism lives in **cubepi** (the agent runtime) as a `DeferredToolGroup` abstraction.
cubeplex provides the application-specific wiring.

**cubepi provides (generic, tool-source-agnostic):**
- `DeferredToolGroup` data structure: `group_id`, `display_name`, `description`, `tool_names`
  (for catalog display), `loader` callback (returns `list[AgentTool]` on expand).
- `agent.register_deferred_group(group)` registration API.
- Catalog text rendering from registered groups (deterministic, sorted by `group_id`).
- `expand_tools(group_id)` builtin tool: validates group_id, invokes loader, injects tools into
  the active set, updates prompt suffix.
- `DeferredToolsMiddleware`: `after_tool_call` records expansion order in `extra`;
  `transform_system_prompt` appends expanded schema text in expansion order (append-only).

**cubeplex provides (MCP-specific):**
- Mapping `MCPRuntimeConnectorSpec` → `DeferredToolGroup` (group_id = `mcp:{slug}`, tool_names
  from `tools_cache`, loader = filtered `load_workspace_mcp_tools_for_cubepi`).
- Threshold decision: which servers to defer (context-window-percentage gate, see §config below).
- Catalog description derivation from `discovery_metadata`.
- Expansion state persistence across turns (via `agent._extra` → checkpointer).

### 1. Catalog index (what's in the prompt by default)

Built from data **already in Postgres** (`tools_cache`, `discovery_metadata`, template/install
`name`/`description`) — no live discovery needed to render it. Rendered as a stable suffix of the
system prompt, sorted by group_id (= server slug for MCP), e.g.:

```
# Connected tool servers (collapsed)

These servers are connected but their tools are not loaded yet. Call
`expand_tools(group_id)` with a group_id below to load that group's tools for the
rest of this conversation.

- `mcp:linear` — Issue tracking (8 tools)
  create_issue, update_issue, search_issues, get_issue, create_project,
  list_projects, create_cycle, list_cycles
- `mcp:gdrive` — Google Drive (5 tools)
  search_files, read_file, list_folders, get_file_metadata, create_file
```

Per-group content:
- **group_id** = `mcp:{slug}` (so `expand_tools("mcp:linear")` is unambiguous).
- **One-line description** = from `discovery_metadata`, trimmed.
- **Tool names** = the namespaced tool names from `tools_cache`, listed without descriptions or
  JSON schemas. Tool names are the highest signal-to-noise ratio element: `search_issues` is more
  precise than "Issue tracking" and far more compact than a full schema. A typical 12-tool server
  adds ~40 tokens of tool names — 10 servers total ~400 tokens, vs 5000+ for full schemas.
- **Tool count** in parentheses.

The catalog never contains per-tool JSON schemas or per-tool descriptions — only tool names.
This is the right trade-off: tool names are self-descriptive (MCP tools follow `verb_noun`
convention), and the model can decide which group to expand just by scanning the name list.
No BM25 or semantic retrieval needed at this catalog size.

The catalog is fully derived from DB state that's identical turn-to-turn, so it's cache-safe as a
suffix.

### 2. Expansion tool: `expand_tools`

A new builtin tool (sibling of `load_skill`), placed in the fixed tool order **where the MCP tools
used to go** (after `load_skill`), so the cache-prefix tool ordering rule is respected. Input:
`{ group_id: str }` (the catalog group_id, e.g. `"mcp:linear"`). Behavior:

- Validate the group_id against registered deferred groups.
- Invoke the group's `loader` callback to obtain callable `AgentTool`s.
- Return a JSON result (analogous to `LoadSkillOutput`) listing the group's tools — the namespaced
  tool names + descriptions, **no schemas in the result** — middleware injects schema text into the
  system-prompt suffix, matching how `SkillsMiddleware` injects skill bodies.
- Record the expanded group_id in `agent._extra["expanded_groups"]` via an `extra_ref` closure.

The model learns about this tool the same way it learns about `load_skill`: the catalog text tells
it to call `expand_tools(group_id)`.

**cubepi owns the tool definition and dispatch.** cubeplex only registers the deferred groups
(with their loaders); the expand tool itself is generic and knows nothing about MCP.

### 3. `DeferredToolsMiddleware` (the cache-safe injector, in cubepi)

A new middleware in **cubepi**, modeled almost exactly on `SkillsMiddleware`:

- **`after_tool_call`**: when the tool is `expand_tools` and it succeeded, append the group_id to
  `extra["expanded_groups"]` **in expansion order** (an ordered list, de-duplicated, preserving
  first-expanded-first). Do **not** sort it — expansion order *is* the cache order.
- **`transform_system_prompt`**: for each expanded group **in expansion order**, append a stable
  section listing that group's full tool definitions (name + description + input schema). Appending
  in expansion order keeps the prefix monotonic and cache-stable: a newly expanded group's block
  always lands *after* every already-rendered block, so earlier cache segments stay byte-identical.

Why schemas go in the **system-prompt suffix**, not the tool list: the tool *list* (`tools=...`) is
fixed before the agent loop starts and is the most cache-sensitive region; mutating it mid-run is
exactly the "toggling MCP tools mid-conversation" the cache doc warns against. Appending schema
text to the system-prompt suffix is the same trick skills already use and is proven cache-safe.

**The catch — actually calling an expanded tool.** Putting a tool's *schema text* in the prompt
does not register a callable `AgentTool`. The naive shortcut — register *all* groups' tools as
real `AgentTool`s up front and merely **omit collapsed groups from the catalog text** — does
**not** save anything. Those tools still flow through `tools=all_tools` into
`create_cubeplex_agent`, so the model still receives every collapsed group's full schema in the
tool block and pays the identical cache-write/cache-read and attention cost as today. Hiding a
tool in the prose while still shipping its schema in `tools=` is not disclosure at all. So
pre-register-all is **rejected**: it is the status quo with a shorter catalog, not a cost win.

To realize the token/cache/attention savings the schemas of collapsed groups must be **absent
from the tool set itself**, not just from the prompt text. v1 therefore commits to **true
deferral (register-on-first-expand)**:

- The `tools=` block at agent-creation time contains **only**: the always-on builtins,
  `expand_tools`, and the tools of any groups already expanded earlier in this conversation
  (replayed from `extra["expanded_groups"]`). Collapsed groups contribute **zero** tool
  definitions and zero schema text.
- When the model calls `expand_tools(group_id)`, the group's `loader` callback is invoked. For
  MCP groups, this calls the real runtime loader —
  `load_workspace_mcp_tools_for_cubepi`-style path, which live-discovers via
  `load_mcp_tools_http(...)` with resolved auth — **filtered to the expanded server(s)**, for the
  remainder of the conversation. `tools_cache` is *not* a tool source: cached JSON schemas are not
  executable. The cache feeds only the lightweight catalog index and the expansion schema text
  appended to the system-prompt suffix per the middleware above.
- Because the cache discipline treats the tool block as fixed per conversation, *adding* tools
  mid-conversation changes that block. We model each expansion as a **cache re-establishment
  point** — the same treatment the discipline doc gives the "new conversation" case — not as a
  silent mid-prefix mutation. This is a bounded, one-time cost per expanded group, far cheaper
  than carrying every collapsed group's schema on every turn from turn one.

**Load-bearing cubepi dependency.** True deferral requires cubepi to support `DeferredToolGroup`
as a first-class concept: registration, catalog rendering, `expand_tools` builtin, mid-run tool
injection + cache re-establishment. This is a **new cubepi feature** (tracked as a separate cubepi
issue), not just a single API call. The scope:
- `DeferredToolGroup` dataclass + `agent.register_deferred_group()` API
- `expand_tools` builtin with loader invocation
- `DeferredToolsMiddleware` (expansion-order tracking + system-prompt suffix injection)
- Mid-run tool-set mutation (add `AgentTool`s to a live agent's context)

If cubepi cannot ship all of this before cubeplex v1, the **fallback** is to keep the tool set
fixed for the lifetime of a single agent run: expansions requested during a run take effect on the
**next** run (next user turn), where the tool set is rebuilt to include all groups expanded so far.
This still delivers the savings (collapsed groups are never in `tools=`) at the cost of a one-turn
delay before a just-expanded group is callable. Either way, pre-register-all is not on the table —
it saves nothing.

### 4. Where it plugs into assembly (cubeplex side)

- `run_manager.py` system-prompt section (~line 1799, beside the skills index): add the catalog
  suffix when disclosure is active.
- `run_manager.py` tool assembly (~line 1034): replace the unconditional "load all MCP tools" call
  to `load_workspace_mcp_tools_for_cubepi` with a **filtered** invocation of that same live loader —
  restricted to the groups expanded so far this conversation (from `extra["expanded_groups"]`),
  never the collapsed ones. For each non-expanded MCP server, register a `DeferredToolGroup` with
  cubepi via `agent.register_deferred_group(...)`.
- cubepi auto-registers the `expand_tools` builtin when deferred groups exist (no manual slot
  management in cubeplex).
- cubepi auto-appends `DeferredToolsMiddleware` when deferred groups are registered.
- Citations: keep `mcp_citation_configs` populated for expanded servers exactly as today; the
  `CitationMiddleware` (~line 1163) is unchanged.

### 5. How the prompt-cache prefix stays intact

- The **catalog** is derived purely from DB state, sorted by group_id → byte-identical every turn.
- **Expanded-group schema text** is appended to the system-prompt **suffix** in **expansion
  order** (never re-sorted), and only ever grows (append-only) within a conversation → matches the
  skills cache pattern, which the cache E2E test already protects. (Skills can sort by name because
  the enabled set is fixed up front and identical every turn; tool groups expand incrementally
  mid-conversation, so id-sorting could insert a later expansion *before* an already-cached block
  and invalidate that prefix — expansion order avoids this.)
- With true deferral, expansion is explicitly modeled as a **cache re-establishment point**
  (treated like the documented "new conversation" case), never as a silent mid-prefix mutation.
- No timestamps, nonces, or per-user dynamic data enter the catalog or schema text.

## Data model / config changes

Mostly reuses existing columns; minimal additions.

- **Reuse:** `MCPConnectorInstall.tools_cache` (schemas — index/preview only, never a tool
  source), `.discovery_metadata`, `.description` (via template), `.slug_name` (catalog key). No
  new schema table strictly required for v1.
- **Possible new field (open):** `trigger_hints: str | None` on `MCPConnectorInstall` (and/or
  template) to author the "Use when:" line, instead of deriving it. If added, follow the migration
  rule: `alembic revision --autogenerate`.
- **Config flags (likely in `mcp` config block):**
  - `mcp.progressive_disclosure.enabled` (`"auto"` | `"on"` | `"off"`, default `"auto"`).
  - `mcp.progressive_disclosure.threshold_pct` (float, default 10.0) — only collapse when
    deferrable tool schemas would consume ≥ this percentage of the active model's context window.
    Borrowed from hermes-agent: this is more direct than a server-count threshold because 3 tiny
    servers don't need deferral while 1 server with 50 large schemas does.
  - `mcp.progressive_disclosure.min_servers` (int, default 2) — secondary guard: never collapse
    when fewer than N servers are connected, even if the token percentage is above threshold.
    Prevents single-server workspaces from getting indirection overhead.
- **No new public-ID table** (no new business entity in v1).
- **Run telemetry (nice-to-have):** record which servers were expanded during a run (in run
  metadata / trace spans) for cost analysis. No schema change if stored in existing extra/trace.

## v1 scope vs later

**v1:**
- **cubepi: `DeferredToolGroup` primitive** — registration API, `expand_tools` builtin, catalog
  rendering, `DeferredToolsMiddleware`, mid-run tool injection. Generic, tool-source-agnostic.
- **cubeplex: MCP → DeferredToolGroup mapping** — MCP servers as groups, catalog descriptions from
  `discovery_metadata`, tool names from `tools_cache`, loader via filtered live MCP discovery.
- Catalog includes per-group tool name lists (not schemas, not descriptions).
- Config-gated: `enabled` (`auto`/`on`/`off`) + `threshold_pct` (context-window percentage) +
  `min_servers` secondary guard. `auto` mode identical to today below threshold.
- **True deferral / register-on-first-expand**: collapsed groups' tools are never in `tools=`;
  expanding a group registers its tools as callable for the rest of the conversation. If cubepi
  cannot ship the full `DeferredToolGroup` feature in time, ship the next-turn fallback (expansions
  take effect on the following user turn) with cubeplex-side middleware, and land the cubepi feature
  as a follow-up. Pre-register-all is explicitly **not** a v1 option — it saves no cache/attention
  cost.

**Later:**
- Non-MCP group types (plugins, large builtin suites) — only a new mapping needed, cubepi
  mechanism is reused.
- Per-tool (sub-group) disclosure for very large single servers.
- Semantic / embedding retrieval over tools (RAG-over-tools) instead of a flat catalog.
- Provider-native deferred tools (Anthropic Tool Search) as an optimization when the active
  provider supports it, sitting behind the same host-level abstraction.
- "Code mode" style search+execute for extreme connector counts.
- Authored `trigger_hints` editing UI in MCP management.
- Unify skills and tool-group disclosure under a single cubepi primitive (both are "catalog →
  expand → inject" patterns; let API stabilize first).

## Testing strategy (E2E-first per CLAUDE.md)

E2E is the priority; MCP can be simulated with a local test MCP server, so there's no excuse to
fall back to fake-server-only unit coverage.

- **Cache regression (the gate).** Extend / mirror `tests/e2e/memory/test_prompt_cache.py`:
  assert the prefix is byte-stable across turns with the catalog present, and across turns after an
  expansion (append-only growth, no mid-prefix mutation). This is the single most important test.
- **E2E disclosure flow.** With ≥ `min_servers` test MCP servers connected: assert (1) only the
  catalog is in the prompt initially (no per-tool schemas), (2) the model can call
  `expand_mcp_server`, (3) after expansion the server's tools are callable and produce results,
  (4) citations still attach for expanded servers.
- **Threshold behavior.** Below `min_servers`, behavior is byte-identical to today (no catalog, all
  tools loaded) — guards the small-workspace path.
- **Determinism unit tests.** Catalog rendering is sorted by slug and stable for a fixed input set;
  expanded-schema rendering is stable for a fixed **expansion sequence** (same servers expanded in
  the same order → byte-identical output, and adding one more expansion only appends). Cheap to
  unit-test alongside the E2E.
- **Per-task incremental runs during dev**, full suite in the pre-PR sweep, per CLAUDE.md.

## Open Questions

### Resolved

- **Disclosure granularity** → tool group (MCP server as v1 group type), not per-tool. Per-tool
  adds too many round trips for our server-sized groups (5-15 tools).
- **Threshold type** → context-window token percentage (default 10%) + min_servers secondary guard,
  not server-count-only. Adopted from hermes-agent.
- **Catalog content** → group_id + one-line description + all tool names (no tool descriptions, no
  schemas). Tool names are the highest signal-to-noise element.
- **cubepi layering** → core mechanism (`DeferredToolGroup`, `expand_tools`, middleware) lives in
  cubepi; cubeplex provides MCP → group mapping + threshold logic + loader callbacks.
- **What does `expand_tools` return?** → Tool names + descriptions only; middleware injects schema
  text into the system-prompt suffix (matching skills pattern).

### Still open

- **cubepi `DeferredToolGroup` API design.** The full scope — registration, catalog rendering,
  expand builtin, middleware, mid-run tool injection — needs a cubepi design pass. Tracked as a
  separate cubepi issue.
- **Trigger hints source.** Derive from description automatically, or add an authored
  `trigger_hints` field (migration + a small editing surface later)? v1 = auto-derivation.
- **Stale `tools_cache`.** Callable tools always come from live discovery on expand, so a stale
  cache cannot make the model call a non-existent tool. The residual staleness risk is only the
  **catalog index** (tool names + description) rendered from the cache: drift there can make the
  model expand the wrong group. Accepted for v1; refresh `tools_cache` out of band.
- **Expanded state persistence across turns.** `extra["expanded_groups"]` must persist as an
  **ordered list** and replay unchanged. If a future store reloads it as an unordered set, the
  rendered prefix could reorder across turns and silently break the cache.
- **Interaction with subagents.** Subagents get their own tool/middleware assembly — should they
  inherit the parent's expanded set, start collapsed, or be configured independently?
- **Disabling mid-conversation / re-collapse.** Not in v1 (monotonic growth is what keeps cache
  safe).
- **Catalog drift defense.** hermes-agent's OpenClaw #84141 lesson: any catalog that can drift from
  the live tool registry silently drops tools. Our defense: callable tools always come from live
  discovery (loader callback), never from catalog data. But catalog text can still mislead the
  model — add an expansion-time validation that warns if the live tool set differs significantly
  from the catalog's tool_names list.

## References

- Prompt-cache discipline: `backend/docs/prompt-cache-discipline.md`
- Agent system design: `backend/docs/agent-system-design.md`
- Skills index/middleware/tool: `backend/cubeplex/prompts/skills.py`,
  `backend/cubeplex/middleware/skills.py`, `backend/cubeplex/tools/builtin/load_skill.py`
- MCP runtime loader: `backend/cubeplex/mcp/cubepi_runtime.py`
- MCP effective service + runtime spec: `backend/cubeplex/mcp/effective.py`
- MCP install model (`tools_cache`, `discovery_metadata`, `slug_name`): `backend/cubeplex/models/mcp.py`
- Tool/prompt assembly: `backend/cubeplex/streams/run_manager.py` (~lines 900, 1034, 1799)
- Prior MCP specs: `docs/dev/specs/2026-05-14-mcp-tools-redesign-design.md`,
  `docs/dev/specs/2026-05-15-mcp-management-four-layer-design.md`,
  `docs/dev/specs/2026-05-14-mcp-tool-citations-design.md`
- Anthropic, "Introducing advanced tool use" (Tool Search Tool / deferred tools):
  https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic API docs, "Tool search tool":
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Unified.to, "Scaling MCP Tools with Anthropic's (& OpenAI's) Defer Loading":
  https://unified.to/blog/scaling_mcp_tools_with_anthropic_and_openai_defer_loading
- Solo.io, "MCP Progressive Disclosure: Save Tokens, Retrieve Schemas":
  https://www.solo.io/blog/mcp-progressive-disclosure
- Matthew Kruczek, "Progressive Disclosure MCP: 85x Token Savings Benchmark":
  https://matthewkruczek.ai/blog/progressive-disclosure-mcp-servers.html
- Layered, "MCP Tool Schema Bloat: The Hidden Token Tax":
  https://layered.dev/mcp-tool-schema-bloat-the-hidden-token-tax-and-how-to-fix-it/
- Philipp Schmid, "Best Practices for Building MCP Servers": https://www.philschmid.de/mcp-best-practices
- Cloudflare, "Code Mode: give agents an entire API in 1,000 tokens":
  https://blog.cloudflare.com/code-mode-mcp/
