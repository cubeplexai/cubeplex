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

cubebox already solved the same shape of problem for **skills**: by default the system prompt
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
- No frontend redesign of MCP management surfaces. A small read-only indicator of which servers
  expanded during a run is acceptable but not required for v1.
- No per-tool (sub-server) disclosure in v1 — the unit of expansion is a whole server.

## Current state in cubebox (how MCP tools reach the prompt today)

The flow, end to end:

1. **Discovery (already cached in DB).** When a connector is installed/refreshed, its tool list is
   discovered and stored on `MCPConnectorInstall.tools_cache`
   (`backend/cubebox/models/mcp.py`, a `list[dict]` of tool definitions including
   `input_schema`). Server/handshake metadata lives in `discovery_metadata`. Per-tool citation
   config lives in `tool_citations`. So **we already have each server's tool schemas in Postgres
   without doing live discovery at prompt-assembly time** — important for building an index cheaply.

2. **Per-run load.** `RunManager._run_cubepi_path` (`backend/cubebox/streams/run_manager.py`,
   ~line 1039) calls `load_workspace_mcp_tools_for_cubepi`
   (`backend/cubebox/mcp/cubepi_runtime.py`). That function:
   - asks `MCPEffectiveConnectorService.list_runtime_specs(...)` for one
     `MCPRuntimeConnectorSpec` per *usable* install (`backend/cubebox/mcp/effective.py`, line 305),
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
   (`backend/cubebox/prompts/skills.py`) — a sorted bullet list of `` `name` — description ``. This
   index is appended as a *stable suffix* of the system prompt so it stays cache-safe.

### The skills precedent (the analog to copy)

- **Index in the prompt.** `run_manager.py` ~line 1799 fetches enabled skills and appends a sorted
  bullet list via `SKILLS_PROMPT_TEMPLATE`. Sorting is what keeps it byte-identical across turns.
- **`load_skill` tool.** `backend/cubebox/tools/builtin/load_skill.py` returns the SKILL.md content
  as a JSON tool result (`LoadSkillOutput`).
- **`SkillsMiddleware`.** `backend/cubebox/middleware/skills.py` watches `after_tool_call` for
  `load_skill`, stashes loaded content into `agent._extra["loaded_skills"]` (via an `extra_ref`
  closure), and on every subsequent model call appends each loaded skill's body to the system
  prompt in `transform_system_prompt` — **sorted by name** for determinism.

The key cache insight from skills: expanded content is appended to the **system prompt suffix**
(after the base prompt, deterministically ordered), so each expansion is a stable, monotonic,
append-only growth of the prefix. Within a turn the prefix is fixed; across turns it only ever
grows by appending — never reorders — so earlier cache segments stay valid.

## Industry research (with citations)

The "load a compact index, expand on demand" pattern is now the mainstream answer to tool/context
bloat. What's transferable to cubebox:

- **Anthropic Tool Search Tool / deferred tools (GA Feb 2026).** You register all tools but mark
  most with `defer_loading: true`; only a search tool plus a few always-on tools are in context.
  The model searches (regex or BM25) and the API returns 3–5 `tool_reference` blocks that expand
  into full definitions. Reported ~85% token reduction (e.g. ~77K → ~8.7K for 50+ MCP tools) and
  large accuracy gains on big tool libraries (Opus 4.5 79.5% → 88.1%). Scales to ~10k tools.
  *Transferable:* validates the index-then-expand shape and confirms expansion should pull only a
  small relevant subset, not everything. *Caveat for us:* this is a provider-side feature on the
  Anthropic API; cubebox runs through cubepi's provider abstraction and multiple providers
  (OpenAI-compatible, deepseek), so we cannot depend on it being present everywhere. Our
  host-level mechanism must work provider-agnostically.

- **MCP host-side filtering / progressive disclosure (general guidance).** Multiple write-ups note
  the host need not forward every discovered tool to the model; it can filter, search, or disclose
  progressively before anything hits context. Standard MCP setups can eat up to ~72% of the
  context window on definitions alone, with tool-selection accuracy dropping as the set grows.
  *Transferable:* cubebox **is** the host here (`RunManager` assembles the tool list), so we own
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

Recommendation drawn from the research: build a **host-side, provider-agnostic** index-then-expand
mechanism modeled on cubebox's own skills system (a catalog in the prompt + an `expand_mcp_server`
tool), rather than binding to any single provider's deferred-tools feature. Keep semantic retrieval
and code-mode as documented later-stage options.

## Proposed design

### Shape: mirror the skills pattern, at the *server* granularity

Default behavior becomes: the prompt carries a **compact MCP catalog**; the model calls a builtin
tool to **expand** a named server; expanded servers' full tools + schemas become available for the
rest of the run. The unit of disclosure is a **server**, not an individual tool — this matches how
users think about MCP ("I connected Linear"), keeps the catalog short, and reuses the existing
per-server namespacing/citation plumbing wholesale.

### 1. Catalog index (what's in the prompt by default)

Built from data **already in Postgres** (`tools_cache`, `discovery_metadata`, template/install
`name`/`description`) — no live discovery needed to render it. Rendered as a stable suffix of the
system prompt, sorted by server slug, e.g.:

```
# Connected tool servers (collapsed)

These servers are connected but their tools are not loaded yet. Call
`expand_mcp_server(server)` with a name below to load that server's tools for the
rest of this conversation.

- `linear` — Issue tracking: create/update/search issues, projects, cycles. (8 tools)
  Use when: tracking work, filing bugs, querying project status.
- `gdrive` — Google Drive: search and read documents, list folders. (5 tools)
  Use when: finding or reading shared docs/spreadsheets.
```

Per-server line content:
- **Name** = the namespacing slug (so `expand_mcp_server("linear")` is unambiguous).
- **One-line description** = install/template `description`, trimmed.
- **Trigger hints** = a short "Use when:" phrase. Source options (open question below): derive from
  description, or add an optional authored `trigger_hints` field on the install/template.
- **Tool count** so the model can gauge cost/coverage.

The catalog never contains per-tool JSON schemas — that's the whole point. It is fully derived
from DB state that's identical turn-to-turn, so it's cache-safe as a suffix.

### 2. Expansion tool: `expand_mcp_server`

A new builtin tool (sibling of `load_skill`), placed in the fixed tool order **where the MCP tools
used to go** (after `load_skill`), so the cache-prefix tool ordering rule is respected. Input:
`{ server: str }` (the catalog slug). Behavior:

- Validate the slug against the workspace's usable installs.
- Return a JSON result (analogous to `LoadSkillOutput`) listing the server's tools — at minimum
  the namespaced tool names + descriptions, and optionally the schemas (open question: does the
  tool *return* schemas, or just acknowledge, letting middleware do the injection?). The design
  keeps schemas out of the tool *result* and lets the middleware add them to the prefix, matching
  how `SkillsMiddleware` injects skill bodies rather than re-emitting them per tool result.
- Record the expanded server in `agent._extra["expanded_mcp_servers"]` via an `extra_ref` closure.

The model learns about this tool the same way it learns about `load_skill`: the catalog text tells
it to call `expand_mcp_server(server)`.

### 3. `MCPDisclosureMiddleware` (the cache-safe injector)

A new middleware modeled almost exactly on `SkillsMiddleware`:

- **`after_tool_call`**: when the tool is `expand_mcp_server` and it succeeded, append the server
  slug to `extra["expanded_mcp_servers"]` **in expansion order** (an ordered list, de-duplicated,
  preserving first-expanded-first). Do **not** sort it — expansion order *is* the cache order.
- **`transform_system_prompt`**: for each expanded server **in expansion order**, append a stable
  section listing that server's full tool definitions (namespaced name + description + input
  schema), rendered from the **cached** `tools_cache` for that install. Appending in expansion
  order keeps the prefix monotonic and cache-stable: a newly expanded server's block always lands
  *after* every already-rendered block, so earlier cache segments stay byte-identical.

Why schemas go in the **system-prompt suffix**, not the tool list: the tool *list* (`tools=...`) is
fixed before the agent loop starts and is the most cache-sensitive region; mutating it mid-run is
exactly the "toggling MCP tools mid-conversation" the cache doc warns against. Appending schema
text to the system-prompt suffix is the same trick skills already use and is proven cache-safe.

**The catch — actually calling an expanded tool.** Putting a tool's *schema text* in the prompt
does not register a callable `AgentTool`. The naive shortcut — register *all* usable servers'
tools as real `AgentTool`s up front and merely **omit collapsed servers from the catalog text** —
does **not** save anything. Those tools still flow through `tools=all_tools` into
`create_cubebox_agent`, so the model still receives every collapsed server's full schema in the
tool block and pays the identical cache-write/cache-read and attention cost as today. Hiding a
tool in the prose while still shipping its schema in `tools=` is not disclosure at all. So
pre-register-all is **rejected**: it is the status quo with a shorter catalog, not a cost win.

To realize the token/cache/attention savings the schemas of collapsed servers must be **absent
from the tool set itself**, not just from the prompt text. v1 therefore commits to **true
deferral (register-on-first-expand)**:

- The `tools=` block at agent-creation time contains **only**: the always-on builtins,
  `expand_mcp_server`, and the tools of any servers already expanded earlier in this conversation
  (replayed from `extra["expanded_mcp_servers"]`). Collapsed servers contribute **zero** tool
  definitions and zero schema text.
- When the model calls `expand_mcp_server(server)`, that server's **callable** `AgentTool`s are
  obtained through the real runtime loader — `load_workspace_mcp_tools_for_cubepi`-style path,
  which live-discovers via `load_mcp_tools_http(...)` with resolved auth — **filtered to the
  expanded server(s)**, for the remainder of the conversation. `tools_cache` is *not* a tool
  source: cached JSON schemas are not executable. The cache feeds only the lightweight catalog
  index and the expansion preview/schema text appended to the system-prompt suffix per the
  middleware above. So the runtime tool-load path that today loads all servers is **filtered to
  expanded servers**, not replaced by synthesizing `AgentTool`s from `tools_cache`.
- Because the cache discipline treats the tool block as fixed per conversation, *adding* tools
  mid-conversation changes that block. We model each expansion as a **cache re-establishment
  point** — the same treatment the discipline doc gives the "new conversation" case — not as a
  silent mid-prefix mutation. This is a bounded, one-time cost per expanded server, far cheaper
  than carrying every collapsed server's schema on every turn from turn one.

**Load-bearing cubepi dependency.** True deferral requires cubepi to support changing a live
agent's callable tool set mid-run (add the newly expanded server's `AgentTool`s) and to re-mark
the cache boundary at that point. If cubepi cannot do this today, that is an **upstream cubepi
change** (cubepi is self-authored, upstream-first) and must land before this feature ships.
The **fallback that needs no cubepi change** is to keep the tool set fixed for the lifetime of a
single agent run: expansions requested during a run take effect on the **next** run (next user
turn), where the tool set is rebuilt to include all servers expanded so far. This still delivers
the savings (collapsed servers are never in `tools=`) at the cost of a one-turn delay before a
just-expanded server is callable. Validate cubepi's mid-run capability before building; if absent,
ship the next-turn fallback for v1 and pursue the cubepi change as a follow-up. Either way,
pre-register-all is not on the table — it saves nothing.

### 4. Where it plugs into assembly

- `run_manager.py` system-prompt section (~line 1799, beside the skills index): add the MCP
  catalog suffix when the feature is enabled and the workspace has ≥ N usable servers.
- `run_manager.py` tool assembly (~line 1034): replace the unconditional "load all MCP tools" call
  to `load_workspace_mcp_tools_for_cubepi` with a **filtered** invocation of that same live loader —
  it still live-discovers callable tools via `load_mcp_tools_http`, but restricted to the servers
  expanded so far this conversation (from `extra["expanded_mcp_servers"]`), never the collapsed
  ones. Filtering the live loader (not building tools from `tools_cache`) is what makes expanded
  tools callable while keeping collapsed servers' schemas out of `tools=all_tools`.
- Register `expand_mcp_server` builtin in the fixed order slot.
- Append `MCPDisclosureMiddleware` to `cubepi_middleware` with an `extra_ref` closure, mirroring
  `SkillsMiddleware` (~line 1263).
- Citations: keep `mcp_citation_configs` populated for expanded servers exactly as today; the
  `CitationMiddleware` (~line 1163) is unchanged.

### 5. How the prompt-cache prefix stays intact

- The **catalog** is derived purely from DB state, sorted by slug → byte-identical every turn.
- **Expanded-server schema text** is appended to the system-prompt **suffix** in **expansion
  order** (never re-sorted), and only ever grows (append-only) within a conversation → matches the
  skills cache pattern, which the cache E2E test already protects. (Skills can sort by name because
  the enabled set is fixed up front and identical every turn; MCP servers expand incrementally
  mid-conversation, so slug-sorting could insert a later expansion *before* an already-cached block
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
  - `mcp.progressive_disclosure.enabled` (bool).
  - `mcp.progressive_disclosure.min_servers` — only collapse when a workspace has at least this
    many usable servers (small workspaces keep today's behavior).
  - Optionally `min_tools` as an alternative threshold.
- **No new public-ID table** (no new business entity in v1).
- **Run telemetry (nice-to-have):** record which servers were expanded during a run (in run
  metadata / trace spans) for cost analysis. No schema change if stored in existing extra/trace.

## v1 scope vs later

**v1:**
- Server-granularity catalog index in the system prompt (DB-derived, sorted, cache-safe).
- `expand_mcp_server` builtin + `MCPDisclosureMiddleware` (skills-pattern port).
- Config-gated by `enabled` + `min_servers`; off-by-default behavior identical to today below the
  threshold.
- **True deferral / register-on-first-expand**: collapsed servers' tools are never in `tools=`;
  expanding a server registers its tools as callable for the rest of the conversation. If cubepi
  cannot change a live agent's tool set mid-run, ship the next-turn fallback (expansions take
  effect on the following user turn) and land the cubepi change as a follow-up. Pre-register-all
  is explicitly **not** a v1 option — it saves no cache/attention cost.

**Later:**
- Per-tool (sub-server) disclosure for very large single servers.
- Semantic / embedding retrieval over tools (RAG-over-tools) instead of a flat catalog.
- Provider-native deferred tools (Anthropic Tool Search) as an optimization when the active
  provider supports it, sitting behind the same host-level abstraction.
- "Code mode" style search+execute for extreme connector counts.
- Authored `trigger_hints` editing UI in MCP management.

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

- **Does cubepi support adding tools to a running agent** and re-establishing the cache boundary
  mid-conversation? This is now the load-bearing residual (the callability approach is decided:
  true deferral, not pre-register-all). If cubepi *can* change a live agent's tool set mid-run, a
  just-expanded server is callable within the same turn. If it *cannot*, v1 ships the next-turn
  fallback (expansion takes effect on the following user turn) and the cubepi change lands as a
  follow-up (cubepi is self-authored, upstream-first). Validate this capability before building.
- **What does `expand_mcp_server` return?** Just an acknowledgement (middleware injects schemas) vs
  the schema list in the tool result itself. Affects token placement and replay/cache behavior.
- **Trigger hints source.** Derive from description automatically, or add an authored
  `trigger_hints` field (migration + a small editing surface later)? Auto-derivation is cheaper but
  lower quality.
- **Granularity.** Is whole-server expansion enough, or do single large servers (50+ tools) need
  per-tool disclosure in v1? Likely v1 = server-only, but confirm against real connector sizes.
- **Threshold default.** What `min_servers` (and/or `min_tools`) value flips collapsing on? Needs a
  token-cost measurement on representative workspaces.
- **Expanded state persistence across turns.** `extra["expanded_mcp_servers"]` lives in agent
  extra and is persisted like `loaded_skills` — confirm it replays correctly so a server expanded
  on turn 1 stays expanded on turn 5 without re-triggering a cache reset each turn. Because the
  cache invariant now depends on **expansion order** (not slug sort), persistence must preserve that
  order too: serialize as an ordered list and replay it unchanged. If a future store reloads it as
  an unordered set, the rendered prefix could reorder across turns and silently break the cache —
  call this out as a constraint on however `extra` is persisted.
- **Stale `tools_cache`.** Callable tools always come from live discovery on expand (the filtered
  runtime loader), so a stale cache cannot make the model call a non-existent tool — the live tool
  set is authoritative. The residual staleness risk is only the **catalog index + expansion
  preview text** rendered from the cache: drift there can make the model expand the wrong server or
  see a description that no longer matches. Open: re-render the preview from the live discovery
  result on expand (perfectly consistent, but couples preview text to per-run discovery and could
  perturb the suffix), or accept cache-rendered preview and refresh `tools_cache` out of band.
- **Interaction with subagents.** Subagents get their own tool/middleware assembly — should they
  inherit the parent's expanded set, start collapsed, or be configured independently?
- **Disabling mid-conversation / re-collapse.** Is there ever a need to collapse an expanded server
  again within a conversation? Probably no for v1 (monotonic growth is what keeps cache safe), but
  state explicitly.

## References

- Prompt-cache discipline: `backend/docs/prompt-cache-discipline.md`
- Agent system design: `backend/docs/agent-system-design.md`
- Skills index/middleware/tool: `backend/cubebox/prompts/skills.py`,
  `backend/cubebox/middleware/skills.py`, `backend/cubebox/tools/builtin/load_skill.py`
- MCP runtime loader: `backend/cubebox/mcp/cubepi_runtime.py`
- MCP effective service + runtime spec: `backend/cubebox/mcp/effective.py`
- MCP install model (`tools_cache`, `discovery_metadata`, `slug_name`): `backend/cubebox/models/mcp.py`
- Tool/prompt assembly: `backend/cubebox/streams/run_manager.py` (~lines 900, 1034, 1799)
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
