# MCP Tool Citations Design

**Date:** 2026-05-14
**Scope:** Backend (data model, runtime, API) + frontend (server-detail tab).
**Status:** Spec — pending implementation plan.

## Summary

MCP tool results are currently never threaded into the citation middleware:
`run_manager.py:712-720` builds `CitationMiddleware(citation_configs={})` with
a hard-coded empty dict. The legacy `mcp.servers.*.tools[].citation` YAML
loader that used to populate this was removed in M2 and never replaced. The
codebase still ships `load_citation_configs()`, but nothing calls it.

This spec wires per-MCP-tool citation mapping back into the system, with two
load-bearing changes:

1. **Citation mapping lives in the database**, layered as catalog defaults
   (read-only, seeded) plus per-install overrides (editable in the workspace).
   The frontend gets a guided editor with field dropdowns. User-added MCP
   servers (no catalog backing) configure their own mapping from scratch.

2. **MCP tool names are namespaced** at load time as
   `{server_name}__{tool_name}`. This is a prerequisite for the citation work
   (the citation registry is keyed by tool name and must be unambiguous
   across installs) and fixes an existing latent bug: today, two MCP servers
   that both expose a `web_search` tool collide silently in the agent's tool
   list with undefined routing.

## Out of scope

- **Test-call sample capture.** The frontend editor renders field dropdowns
  from a stored response sample where one exists, but the feature that
  *captures* that sample (a "Test call" action on the existing MCP tools
  tab, persisting the response shape to the server row) is a separate,
  sister feature. The editor degrades to a text-input fallback with a link
  to the tools tab when no sample exists.
- **Catalog-defaults editing UI.** Catalog entries are seed-authored; the
  source of truth is `catalog_seed.py`. No runtime write endpoint.
- **Auto-cascade on catalog upgrade.** Updating a catalog seed entry's
  `tool_citations` does not retroactively rewrite already-installed
  `mcp_servers` rows. Users opt in per-tool via "Reset to catalog default"
  in the editor.
- **Citation type extension** (new `source_type` values). The model accepts
  any string; no code change needed.

## Architecture

```
mcp_catalog_connectors.tool_citations  ← seeded read-only defaults
        ↓ shallow-copied at install time
mcp_servers.tool_citations             ← effective, workspace-editable
        ↓ read by per-run loader, namespaced into:
load_workspace_mcp_tools_for_cubepi → (tools, citation_configs)
                                        ↓
                            run_manager → CitationMiddleware
```

Tool name namespacing happens in
`cubebox/mcp/cubepi_runtime.load_workspace_mcp_tools_for_cubepi`: after
cubepi returns `list[AgentTool]` for each server, each tool's `name` is
mutated to `f"{server.name}__{tool.name}"`. The same function emits the
matching `dict[namespaced_name, CitationConfig]` for the middleware.

`tool_citations` keys are stored as **bare tool names** in the DB (they're
local to a single server); the namespace prefix is added only at runtime
join time. This keeps editing logic simple — the frontend edits bare keys
under one server — and avoids storing computed identifiers.

## Data model

### `mcp_catalog_connectors`

Add column:

```python
tool_citations: dict[str, dict[str, Any]] = Field(
    default_factory=dict,
    sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
)
```

Key = bare tool name. Value = JSON-serialized `CitationConfig`.

### `mcp_servers`

Add column with identical shape and default. Independent column (not
nested in `tools_cache`) so the discovery refresh that rewrites
`tools_cache` does not clobber user-edited citation mapping.

### `CitationConfig` (`cubebox/middleware/citations/config.py`)

Add `content_type: Literal["json", "text"] = "json"`. The chunker already
assumes JSON; this flag makes the text path explicit (needed for
`web_fetch`-style tools that return raw text).

Final shape of one entry:

```jsonc
{
  "content_type": "json",
  "source_type": "web",
  "content_field": "results",
  "mapping": {
    "url": "url",
    "title": "title",
    "snippet": "description"
  },
  "args_mapping": null,
  "discriminator_field": null,
  "discriminator_values": null
}
```

Empty `{}` on `tool_citations` = no tool from this server produces
citations; same effect as today's default behavior.

### `CatalogSeedEntry` (`cubebox/mcp/catalog_seed.py`)

Add `tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)`.
Seed the field for known connectors (`webtools`: `web_search` + `web_fetch`
to start; others left empty until their citation shape is needed).

## Data flow

### Seed (deploy time, one-off)

`python -m cubebox.cli seed-mcp-catalog` upserts each
`CatalogSeedEntry.tool_citations` into
`mcp_catalog_connectors.tool_citations`. Repeating the command after a seed
change updates the catalog row in place; it does **not** propagate to
existing installs (see §Out of scope).

### Install (user installs from catalog into workspace)

In `services/mcp_catalog.py` (or wherever an install service exists today),
the install step that creates the `MCPServer` row also does:

```python
new_server.tool_citations = dict(catalog.tool_citations)
```

This is a one-shot snapshot. From here on, the install row is decoupled
from the catalog; the user owns its citation mapping.

Manually-added MCP servers (no `catalog_connector_id`) default to `{}`.
User fills it in via the editor.

### Discovery refresh (admin sync-tools / OAuth callback)

`MCPServerRepository.refresh_server_tools` rewrites `tools_cache`. After
the rewrite, compare `tool_citations` keys against the new
`{tool.name for tool in tools_cache}`:

- **Keep** entries for tools still present.
- **Delete** entries pointing at vanished tools; write a single-line
  notice to `last_error` ("Removed citation mapping for vanished tools:
  [x, y]"). Do not block the refresh.
- **Do not auto-create** entries for newly-discovered tools — the user
  decides whether they need citations.

This makes refresh idempotent and friendly to user edits.

### Per-run load (every agent run)

`load_workspace_mcp_tools_for_cubepi`
(`cubebox/mcp/cubepi_runtime.py`) is rewritten to return both tools and
citation configs:

```python
async def load_workspace_mcp_tools_for_cubepi(
    ...,
) -> tuple[list[AgentTool], dict[str, CitationConfig]]:
    servers = await discover_workspace_mcp_servers_for_cubepi(...)
    all_tools: list[AgentTool] = []
    all_citations: dict[str, CitationConfig] = {}
    for spec in servers:
        try:
            tools = await load_mcp_tools_http(
                spec.url, headers=spec.headers or None, timeout=30.0,
            )
        except Exception as exc:
            logger.warning("Failed to load MCP server %s: %s", spec.server_name, exc)
            continue
        prefix = f"{spec.server_name}__"
        for t in tools:
            bare_name = t.name
            t.name = f"{prefix}{bare_name}"
            all_tools.append(t)
            raw = (spec.tool_citations or {}).get(bare_name)
            if raw is None:
                continue
            try:
                all_citations[t.name] = CitationConfig(**raw)
            except ValidationError as exc:
                logger.warning(
                    "Bad tool_citations on %s/%s: %s",
                    spec.server_name, bare_name, exc,
                )
    return all_tools, all_citations
```

`discover_workspace_mcp_servers_for_cubepi`'s `ServerSpec` already carries
`server_name` and `server_id`; this spec assumes it adds (or already has)
a `tool_citations: dict` field surfaced from the DB row. If not, that's a
trivial addition.

### Run wiring (`run_manager.py:712-720`)

```python
# 3. CitationMiddleware
mcp_tools, mcp_citation_configs = await load_workspace_mcp_tools_for_cubepi(...)
# (other middleware unchanged)
cubepi_middleware.append(
    CitationMiddleware(
        citation_configs=mcp_citation_configs,
        event_queue=citation_event_queue.get(None),
    )
)
```

Built-in tools (e.g. `file_read`) keep contributing their citation config
via `load_builtin_citation_configs` exactly as today; runtime merge
order: builtin first, then MCP, with MCP entries last-wins on bare-name
collision (which by namespacing should now be impossible across MCP
servers; only a builtin and an MCP namespaced-tool can share a name,
which won't happen given the `__` separator convention).

### Legacy langchain path (`cubebox/mcp/discovery.py`)

The admin/OAuth sync-tools path still uses `langchain-mcp-adapters`. To
keep tool names consistent across paths (so the frontend tools tab and
the agent see the same names), `_build_basetool_for_entry` also
namespaces:

```python
StructuredTool.from_function(
    ...,
    name=f"{server.name}__{entry['name']}",
    ...
)
```

The bare name remains in `tools_cache[i].name` (that's the protocol-level
name and remains the editing key for `tool_citations`).

## API

Three new endpoints. Two scoped to workspace (read + write for one
install), one global read for catalog defaults.

### `GET /api/v1/ws/{wsId}/mcp/servers/{serverId}/tool-citations`

Workspace-member read. Returns:

```jsonc
{
  "server_id": "mcp-...",
  "server_name": "webtools",
  "tools_cache": [
    { "name": "web_search", "description": "...", "input_schema": {...} },
    { "name": "web_fetch",  "description": "...", "input_schema": {...} }
  ],
  "tool_citations": {
    "web_search": { ...CitationConfig JSON... }
  },
  "catalog_defaults": {
    "web_search": { ... },
    "web_fetch": { ... }
  },
  "orphan_keys": []
}
```

- `tools_cache` is whatever the existing column holds (admin/OAuth-refresh
  path is the writer).
- `catalog_defaults` is `null` for manually-added servers (no
  `catalog_connector_id`).
- `orphan_keys` lists keys in `tool_citations` that don't appear in
  `tools_cache.name`. Normally empty (refresh cleans them, §Discovery
  refresh), but surfaced for resilience.

### `PATCH /api/v1/ws/{wsId}/mcp/servers/{serverId}/tool-citations`

Workspace-admin write. Body:

```jsonc
{ "tool_citations": { "web_search": { ... }, ... } }
```

Behavior:

- Full replacement (no field-level merge — frontend sends the complete
  dict).
- Empty `{}` is allowed (disables citation for the server).
- Every value is validated as a `CitationConfig` (pydantic). Failure →
  `422` with a per-key error list.
- Keys not in `tools_cache.name` are rejected (`422`). Use `tools_cache`
  rather than live discovery to make the endpoint deterministic.
- On success returns the same shape as `GET`.

### `GET /api/v1/ws/{wsId}/mcp/catalog/{slug}/tool-citations`

Workspace-member read. Catalog content itself is org-agnostic but the
endpoint stays under `/ws/{wsId}` to match the existing catalog
member-facing routes (`mcp_catalog.py` already exposes catalog reads
under that prefix). Returns:

```jsonc
{ "slug": "webtools", "tool_citations": { ... } }
```

Used by the "Reset to catalog default" flow in the editor.

### Permissions

| Endpoint | Role |
|---|---|
| `GET` server tool-citations | workspace member |
| `PATCH` server tool-citations | workspace admin |
| `GET` catalog tool-citations | workspace member |

## Frontend UX

Mount point: extend `MCPServerDetail.tsx` with a new tab "Citation
mapping" alongside the existing Tools tab.

### Layout

Master-detail inside the tab:

```
┌── 30% tool list ─────────┬── 70% editor ────────────────────────────┐
│ ✓ web_search             │  web_search                              │
│ ✓ web_fetch              │  ───────────                             │
│ ⚪ get_status             │  [Disable] [Reset to catalog default]   │
│ ⚠ old_tool (orphan)      │                                          │
│                          │  Source type: [web ___]   〈web|file|doc〉│
│                          │  Content type: ○ json  ● text             │
│                          │                                          │
│                          │  Result location                          │
│                          │  ○ Whole response is one item             │
│                          │  ● Array at: [results ▾]                  │
│                          │                                          │
│                          │  Metadata mapping                         │
│                          │   url       = [url       ▾]   [×]        │
│                          │   title     = [title     ▾]   [×]        │
│                          │   snippet*  = [description▾]   [×]        │
│                          │   [+ add field]                           │
│                          │                                          │
│                          │  ▸ Args fallback (collapsed)              │
│                          │  ▸ Discriminator filter (collapsed)       │
└──────────────────────────┴──────────────────────────────────────────┘
                                                  [Save changes]
```

Status badges per tool in the list:

- `✓` has mapping
- `⚪` not configured
- `⚠` orphan (`tool_citations` key without a matching `tools_cache`
  entry) — provides only a "Remove" affordance

### Field dropdowns

`content_field` and per-row metadata-value dropdowns get candidates from
a captured response sample (stored by the sister test-call feature; see
§Out of scope). When no sample is available the values revert to text
inputs with helper text: *"No response sample yet. Go to Tools tab →
Test call to capture one."* with a link.

`args_mapping` value dropdowns always use `tools_cache[tool].input_schema.properties`
keys — no sample needed.

### Save semantics

Component state holds a `dirty_tool_citations` dict initialized from the
server's current state. Save sends the whole dict via PATCH (matches the
API's full-replacement contract). Save button highlights only while the
component is dirty. Navigation away while dirty triggers the standard
unsaved-changes confirm.

### Reset / copy

- **Reset to catalog default** (button, catalog-backed servers only): pulls
  `GET /api/v1/mcp/catalog/{slug}/tool-citations` and overlays the
  current tool's editor state. Does not auto-save; user confirms via Save.
- **Copy from another server** (dropdown, useful primarily for manually-added
  servers): lists other installs in the same workspace that expose a
  same-named tool with a non-empty mapping. Selecting one overlays the
  editor state.

### Permissions

Members see the tab read-only (no Save, no Reset, no edits to inputs).
Admins get full editor.

### i18n

New namespace `mcp.serverDetail.citations.*` for tab title, field labels,
placeholders, helper texts, and error messages.

## Migration & rollout

### Schema migration

One alembic revision adds `tool_citations` to both tables with
`server_default '{}'`, nullable=False. Downgrade drops both columns.
Existing rows transparently get empty dicts; runtime behavior is
unchanged for any install that doesn't subsequently get edits.

### Deploy order

1. `alembic upgrade head`
2. `python -m cubebox.cli seed-mcp-catalog`

No automated backfill of existing `mcp_servers` rows from the freshly
seeded catalog. Users who want catalog defaults applied to an existing
install hit "Reset to catalog default" per tool in the editor.

Rationale: keeps the migration trivial, preserves user edits, and matches
the broader rule that catalog → install propagation is always an
explicit user action.

### Tool-name change & prompt cache

Namespacing changes the tool name byte sequence in the system prompt's
stable prefix. Every active conversation pays one cache-miss turn at
deploy time; subsequent turns re-cache normally. This is a content
version bump, not a prompt-cache discipline violation (no per-turn
dynamic content is being introduced).

`tests/e2e/memory/test_prompt_cache.py` should remain green — it runs
fresh conversations whose prefix is stable post-namespacing. If a
fixture string-asserts on a tool name (e.g. `"web_search"`), update it
to the namespaced form.

### Tool-call replay

Conversation history may contain assistant messages with
`tool_call(name="web_search", ...)` from before deploy. The current tool
list will have `webtools__web_search` instead. Modern LLMs match by
description in this case, so a one-time mild regression in tool-call
fidelity is the realistic worst case. No checkpoint rewrite — cubepi
treats messages as immutable. If field reports surface confusion, a
one-line bridge in the system prompt is the fallback.

## Test surface

### New

- `tests/unit/test_catalog_seed.py` — assert `webtools` entry has
  non-empty `tool_citations` of the expected shape.
- `tests/unit/test_citation_config.py` — `content_type` default,
  round-trip, invalid value rejection.
- `tests/unit/mcp/test_namespace.py` — `load_workspace_mcp_tools_for_cubepi`
  returns namespaced tool names; matching citation_configs keys; bare
  tool name preserved in `tools_cache`.
- `tests/unit/mcp/test_refresh_orphan_cleanup.py` — refresh that loses a
  tool also strips the orphan citation key and writes the notice.
- `tests/e2e/test_mcp_tool_citations.py`:
    - Catalog install copies `tool_citations` into the new server row.
    - Agent run on a tool with mapping produces citations in the SSE
      stream.
    - PATCH the mapping → next run reflects the change.
    - PATCH with an unknown key → 422.

### Updated

- Any existing E2E or fixture that hard-codes a bare MCP tool name in
  assertions or LLM transcripts.

## File-by-file change list

Backend:

1. `backend/alembic/versions/<ts>_add_tool_citations_to_mcp_tables.py` — new.
2. `backend/cubebox/models/mcp.py` — add column on both models.
3. `backend/cubebox/repositories/mcp_catalog.py` — `upsert_by_slug` accepts
   `tool_citations`.
4. `backend/cubebox/services/mcp_catalog.py` — install copies
   `catalog.tool_citations` into new `MCPServer` row.
5. `backend/cubebox/mcp/catalog_seed.py` — extend `CatalogSeedEntry`; fill
   `webtools` (and any other known) entries; seed_catalog passes the field
   through.
6. `backend/cubebox/middleware/citations/config.py` — add `content_type`;
   reuse `load_citation_configs` (rewritten if its input shape changes).
7. `backend/cubebox/mcp/cubepi_runtime.py` — namespace tool names, emit
   citation configs, change return signature.
8. `backend/cubebox/mcp/cubepi_discovery.py` — surface `tool_citations` on
   `ServerSpec` if not already present.
9. `backend/cubebox/mcp/discovery.py` — namespace
   `_build_basetool_for_entry` to match.
10. `backend/cubebox/streams/run_manager.py:712-720` — replace
    `citation_configs={}` with the loader's emitted dict.
11. `backend/cubebox/api/routes/v1/ws_mcp.py` — add the two
    per-server endpoints (`GET` + `PATCH`
    `/ws/{wsId}/mcp/servers/{serverId}/tool-citations`).
12. `backend/cubebox/api/routes/v1/mcp_catalog.py` — add the catalog
    read endpoint
    (`GET /ws/{wsId}/mcp/catalog/{slug}/tool-citations`) on the existing
    `catalog_member_router`.

Frontend:

13. `frontend/packages/core/src/types/mcp.ts` — add
    `ToolCitationsResponse`, `CitationConfig` JSON shape.
14. `frontend/packages/core/src/api/mcp.ts` — add `getToolCitations`,
    `patchToolCitations`, `getCatalogToolCitations`.
15. `frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx` — new.
16. `frontend/packages/web/components/mcp/MCPCitationEditor.tsx` — new.
17. `frontend/packages/web/components/mcp/MCPCitationFieldRow.tsx` — new.
18. `frontend/packages/web/components/mcp/MCPServerDetail.tsx` — register
    the new tab.
19. `frontend/packages/web/__tests__/e2e/mcp/citation-mapping.spec.ts` — new.
20. i18n message files — add `mcp.serverDetail.citations.*`.

## Risks & rollback

| Risk | Mitigation |
|---|---|
| LLM fails to match historical bare `tool_call` names against namespaced tools | Monitor first-turn tool-call failure rate post-deploy. Fallback: add a bridge sentence in the system prompt. |
| One-shot prompt-cache invalidation across all live conversations | Documented expected behavior. One turn of full-price tokens; not a regression. |
| Invalid `CitationConfig` in a server row crashes the loader | Per-entry try/except, single bad entry skips itself without affecting other tools. PATCH validates before write. |
| User mistakenly expects catalog seed changes to propagate to existing installs | Frontend "Reset to catalog default" is the only propagation surface; documented in editor helper text. |

Rollback order if needed: revert code first, then `alembic downgrade -1`
(otherwise the seed entry's new field crashes the dataclass at startup).

## Decision log

- **Storage layout: catalog defaults + per-install override.** Chosen
  over catalog-only (user-added servers couldn't have citations) and
  server-only (catalog upgrades invisible). Allows defaults without
  blocking customization.
- **Per-install column, not nested in `tools_cache`.** Discovery refresh
  rewrites `tools_cache`; a nested citation field would be repeatedly
  clobbered.
- **Tool namespace separator `__`.** OpenAI strict function-name regex
  is `^[a-zA-Z0-9_]+$`, ruling out `::`, `/`, and `.`. Double underscore
  is unambiguous against single-underscore tool names already in use.
- **Stored citation keys are bare, namespacing applied at runtime.** Keeps
  the DB column local to a single server's domain and avoids storing
  derived strings.
- **PATCH is full-replacement.** Simpler client (always send the whole
  dict) and unambiguous semantics. Cost negligible at expected dict
  sizes (KB).
- **No auto-cascade from catalog to existing installs.** Preserves user
  edits; matches established product norm (cred/scope settings don't
  cascade either).
- **No catalog-edit endpoint.** Catalog is seed-authored; changing it
  goes through code review + redeploy.
