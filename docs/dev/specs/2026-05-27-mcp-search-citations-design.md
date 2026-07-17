# Search MCP Sources + Citation Rules Design

**Date:** 2026-05-27 (revised 2026-05-28)
**Issue:** #148
**Scope:** Backend catalog seed for 3 hosted search MCPs (Tavily, Exa, Jina),
new runtime static-auth plumbing so non-Bearer providers can authenticate,
real-shape unit tests for citation extraction, and a minimal citation-prompt
calibration. Bocha and Perplexity are deferred (no usable hosted MCP at PR
time).
**Status:** Spec — implemented in PR #159.

## Problem & motivation

Today the catalog ships connectors for productivity tools (GitHub, Notion,
Slack, …) plus a self-hosted `webtools` server. There is no curated, ready-to-
install **search** source — a user who wants the agent to answer from the open
web, academic papers, code, or news has to find an MCP server, paste a URL,
and hand-author its citation mapping from scratch.

At the same time, the citation discipline (`【N-M】` markers wired through
`CitationMiddleware`) already exists and works for `webtools`. Its value is
highest exactly for search results: a search answer the user cannot trace back
to a source URL/title is low-trust. So the natural next step is to bundle
dedicated search connectors **and** seed their citation config so that, out of
the box, answers built from search results carry visible source attribution.

This feature does three things:

1. Add hosted search MCP connectors to the seed catalog
   (`backend/cubeplex/mcp/template_seed.py::CATALOG`), each with
   `tool_citation_defaults` filled in for its search tools.
2. Extend the runtime so the `static` auth path supports **Bearer** (the
   existing default), **custom header** (e.g. `x-api-key`), and **URL
   query-param** (e.g. `?tavilyApiKey=…`). Provider auth shape is data on
   the template/install row, not branching in code.
3. Calibrate the shared citation prompt
   (`backend/cubeplex/prompts/citations.py::CITATION_PROMPT`) with one extra
   rule + one worked search example so the model reliably emits visible
   `【N-M】` markers for search-result facts.

## Goals / Non-goals

**Goals**

- Curate hosted search MCP connectors and seed them with correct transport,
  auth shape, and `tool_citation_defaults` driven by each provider's actual
  result JSON.
- Plumb non-Bearer static auth (custom header + URL query-param) through the
  runtime so providers like Exa (`x-api-key`) and Tavily (`?tavilyApiKey=…`)
  can ship as catalog entries without runtime hacks.
- Extend `CitationConfig.extract_items` to walk a dotted `content_field`
  (`data.webPages.value`) so providers nesting results under metadata wrappers
  can be cited cleanly when added later (Bocha is the motivating exemplar).
- Calibrate `CITATION_PROMPT` so visible source URL/title attribution lands in
  the final answer the user reads, across our supported models.
- Keep the system-prompt stable prefix prompt-cache-safe.
- Verify each shipped provider against its real REST/MCP endpoint with a real
  key; capture the response JSON as a unit-test fixture so a silent
  provider-side schema change fails CI before a workspace fails at runtime.

**Non-goals**

- No new citation data model or runtime change to the citation pipeline
  itself (the 2026-05-14 spec already shipped the column, middleware, and
  per-run loader). The auth plumbing IS new, but it is auth, not citations.
- No per-server custom prompt. One shared citation prompt; calibration is
  global. (Open question OQ-3.)
- No frontend work. The citation-mapping editor and citation panel already
  exist from the 2026-05-14 spec.
- No self-hosting a search aggregator. We point at hosted remote MCP servers;
  the user supplies their own API key per install.
- No auto-ranking / re-ranking of search results. We pass through whatever the
  server returns.
- No flaky E2E asserting visible `【N-M】` markers in the final assistant text.
  Model citation adherence is unreliable in practice; the citation side-channel
  (`citation` SSE event + `details["citations"]`) and the captured-structure
  unit tests are the supported regression surface.

## Current citation mechanism in cubeplex

How a tool's citation behavior is configured today, end to end:

1. **Catalog seed** — `backend/cubeplex/mcp/template_seed.py`. Each
   `MCPConnectorTemplateSeedEntry` carries
   `tool_citation_defaults: dict[str, dict]`, keyed by **bare tool name**, with
   each value a JSON-serialized `CitationConfig`. The `webtools` entry is the
   reference: it maps `web_search` (`content_field="results"`,
   `mapping={url,title,snippet→description}`) and `web_fetch`
   (`content_type="text"`, `args_mapping={url:url}`).

2. **Citation config shape** —
   `backend/cubeplex/middleware/citations/config.py::CitationConfig`. Fields:
   `content_type` (`"json"|"text"`), `source_type` (free string, e.g. `"web"`),
   `content_field` (path to the result array — now supports either a single
   key like `"results"` OR a dotted path like `"data.webPages.value"`; `None`
   means "treat the whole response as one result"), `mapping`
   (citation-metadata-key → result-field; the special `snippet` key names the
   text field that gets chunked), `args_mapping` (fallback from tool call
   args), `discriminator_field` / `discriminator_values` (filter result items).

3. **Seed → DB** — `seed_templates()` upserts `tool_citation_defaults` into
   `mcp_connector_templates.tool_citations` via `repo.upsert_by_slug(...)`. The
   column was added in
   `backend/alembic/versions/94630a9e13b4_add_tool_citations_to_mcp_tables.py`.

4. **Install → per-install override** — installing a catalog connector
   snapshots `tool_citations` onto the new `mcp_servers` row; from there it is
   workspace-editable and decoupled from the catalog. The same snapshot
   pattern carries the new auth fields (`static_auth_style`,
   `static_auth_header_name`, `static_auth_query_param`) onto the install row.

5. **Per-run load** — `load_workspace_mcp_tools_for_cubepi` namespaces tool
   names to `{server}__{tool}` and emits a
   `dict[namespaced_name, CitationConfig]` for the middleware.

6. **Runtime** — `backend/cubeplex/middleware/citation.py::CitationMiddleware`:
   - `transform_system_prompt` appends `CITATION_PROMPT` only when ≥1 citation
     config is registered.
   - `after_tool_call` parses each tool result per its `CitationConfig`, chunks
     the snippet text, assigns session-incrementing `【N-M】` ids, rewrites the
     LLM-visible content to `【N-M】 [url: … | title: …] chunk`, and emits the
     structured citation on the SSE side channel + `details["citations"]`.

## Search MCP candidates (verified 2026-05-27 against live endpoints)

| Name | Hosted MCP endpoint | Auth (verified) | Ship in v1? | Notes |
|---|---|---|---|---|
| **Tavily** | `https://mcp.tavily.com/mcp/` | `Authorization: Bearer` (also accepts `?tavilyApiKey=…`) | YES | Both auth shapes work; seed uses Bearer for catalog parity. |
| **Exa** | `https://mcp.exa.ai/mcp` | `x-api-key` | YES | Bearer is NOT accepted; runtime now plumbs the `header` style. |
| **Jina AI** | `https://mcp.jina.ai/v1` | `Authorization: Bearer` | YES | 19 tools incl. `search_web`, `read_url`, `search_arxiv`. |
| **Bocha / 博查** | (none official) | `Authorization: Bearer` (REST) | NO — defer | No official hosted MCP. Third-party gateway `mcp.ecn.ai/{CID}/bochaai/mcp` exists but is not Bocha-operated. REST shape captured as a unit-test fixture so we're ready when an official MCP lands. |
| **Perplexity** | (deprecated) | `Authorization: Bearer` (REST) | NO — defer | Perplexity CTO publicly moved away from MCP in favour of their Agent API. No durable hosted MCP endpoint to point at. |

Verification commands (run inline with a real key; nothing committed):

```bash
# Tavily MCP — initialize over Streamable HTTP with Bearer
curl -sS -X POST https://mcp.tavily.com/mcp/ \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-06-18" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'  # → HTTP/2 200

# Exa MCP — same handshake but with x-api-key
curl -sS -X POST https://mcp.exa.ai/mcp \
  -H "x-api-key: $EXA_API_KEY" ...  # → HTTP/2 200 + Mcp-Session-Id

# Jina MCP — Bearer
curl -sS -X POST https://mcp.jina.ai/v1 \
  -H "Authorization: Bearer $JINA_API_KEY" ...  # → HTTP/2 200
```

REST result shapes (captured fixtures live in
`backend/tests/unit/fixtures/search_responses/`):

```jsonc
// Tavily REST: {query, answer, results: [{url, title, content, score, raw_content, ...}], ...}
// Exa REST:    {requestId, results: [{id, title, url, publishedDate, text, image, favicon}], ...}
// Bocha REST:  {code, msg, data: {webPages: {value: [{id, name, url, snippet, summary, siteName, datePublished, ...}]}, ...}}
// Jina REST:   {code, status, data: [{title, url, description, content, publishedTime, metadata, ...}], meta}
```

## Proposed design

### Which connectors ship in v1 (revised)

Three hosted search MCPs, each verified against its live endpoint with a real
API key on 2026-05-27 and seeded with the correct auth shape:

1. **Tavily** (slug `tavily`) — `static_auth_style="bearer"`, tools
   `tavily_search` + `tavily_extract`, results at `results[]`.
2. **Exa** (slug `exa`) — `static_auth_style="header"`,
   `static_auth_header_name="x-api-key"`, tools `web_search_exa`,
   `research_paper_search_exa`, `code_search_exa`, `web_fetch_exa`, results
   at `results[]`.
3. **Jina AI** (slug `jina`) — `static_auth_style="bearer"`, tools
   `search_web`, `search_arxiv`, `search_ssrn`, `read_url`, results at
   `data[]`.

Bocha and Perplexity are NOT seeded — see Open Questions OQ-A / OQ-B.

### Auth-plumbing schema

`MCPConnectorTemplate` gains three columns; the same three are snapshotted
onto `MCPConnectorInstall` at install time (parallel to the existing
`tool_citations` snapshot — workspaces edit their copy, the catalog row
stays canonical):

| Column | Type | Meaning |
|---|---|---|
| `static_auth_style` | `VARCHAR(16)` NOT NULL DEFAULT `'bearer'` | `"bearer"` / `"header"` / `"query"` — dispatches the static branch in `cubepi_runtime.py`. |
| `static_auth_header_name` | `VARCHAR(64)` NULL | When `style="header"`, the header carrying the raw token (e.g. `x-api-key`, `X-Lark-MCP-UAT`). |
| `static_auth_query_param` | `VARCHAR(64)` NULL | When `style="query"`, the URL query-param name (e.g. `tavilyApiKey`). |

Migration:
`backend/alembic/versions/49844ea5ac7b_mcp_template_install_static_auth_style_.py`
(autogenerated, `op.add_column` only — additive, default-backed, no data
backfill required for existing rows).

### Runtime auth dispatch (`backend/cubeplex/mcp/cubepi_runtime.py`)

`_resolve_headers_from_spec` is renamed to `_resolve_auth_from_spec` and
returns `(headers, server_url)` instead of just `headers` — the query-param
style needs to rewrite the URL so the credential rides on every JSON-RPC
request to that connector. Two pure helpers do the real work:

- `_apply_static_credential(spec, headers, server_url, plaintext)` —
  dispatches on `spec.static_auth_style`:
  - `"bearer"` → `headers["Authorization"] = f"Bearer {plaintext}"`.
  - `"header"` → `headers[spec.static_auth_header_name] = plaintext`.
  - `"query"`  → returns the URL with `spec.static_auth_query_param=plaintext`
    appended via `_inject_query_param`.
  - Missing header/param name OR an unknown style → fall back to Bearer with
    a `logger.warning` so a misconfigured install still talks instead of
    silently 401-ing.
- `_inject_query_param(server_url, name, value)` — appends or replaces
  `name=<value>` on the URL's query string; preserves other params.

`load_workspace_mcp_tools_for_cubepi` and every other call site
(`ws_mcp.py::invoke`, `admin_mcp.py::admin_invoke`,
`mcp_discovery.py::discover_tools_for_install`) consume the new
`(headers, server_url)` tuple and hand the rewritten URL to
`load_mcp_tools_http` / `_list_raw_mcp_tools` / `_invoke_tool_via_cubepi`.

### `extract_items` dotted-path support

`CitationConfig.extract_items` previously did a single `data.get(content_field, [])`
lookup. It now walks `content_field.split(".")` step by step, returning `[]` if
any segment is missing or non-dict. Existing single-key `content_field` values
keep their behaviour (no migration of seed data needed). The change unblocks
shipping Bocha (and any future provider that wraps results under metadata) the
day there's a usable hosted MCP for it.

### Citation config per connector

Tavily (`tavily`):
```jsonc
{
  "tavily_search":  {"content_type": "json", "source_type": "web",
                     "content_field": "results",
                     "mapping": {"url": "url", "title": "title", "snippet": "content"}},
  "tavily_extract": {"content_type": "json", "source_type": "web",
                     "content_field": "results",
                     "mapping": {"url": "url", "snippet": "raw_content"}}
}
```

Exa (`exa`):
```jsonc
{
  "web_search_exa":           {"content_type": "json", "source_type": "web",
                               "content_field": "results",
                               "mapping": {"url": "url", "title": "title", "snippet": "text"}},
  "research_paper_search_exa": {"content_type": "json", "source_type": "academic",
                               "content_field": "results",
                               "mapping": {"url": "url", "title": "title", "snippet": "text"}},
  "code_search_exa":          {"content_type": "json", "source_type": "code",
                               "content_field": "results",
                               "mapping": {"url": "url", "title": "title", "snippet": "text"}},
  "web_fetch_exa":            {"content_type": "json", "source_type": "web",
                               "content_field": "results",
                               "mapping": {"url": "url", "title": "title", "snippet": "text"}}
}
```

Jina (`jina`):
```jsonc
{
  "search_web":    {"content_type": "json", "source_type": "web",
                    "content_field": "data",
                    "mapping": {"url": "url", "title": "title", "snippet": "description"}},
  "search_arxiv":  {"content_type": "json", "source_type": "academic",
                    "content_field": "data",
                    "mapping": {"url": "url", "title": "title", "snippet": "description"}},
  "search_ssrn":   {"content_type": "json", "source_type": "academic",
                    "content_field": "data",
                    "mapping": {"url": "url", "title": "title", "snippet": "description"}},
  "read_url":      {"content_type": "json", "source_type": "web",
                    "content_field": "data",
                    "mapping": {"url": "url", "title": "title", "snippet": "content"}}
}
```

Bocha (NOT seeded — captured for the day Bocha ships a hosted MCP):
```jsonc
{
  "bocha_web_search": {"content_type": "json", "source_type": "web",
                       "content_field": "data.webPages.value",
                       "mapping": {"url": "url", "title": "name", "snippet": "snippet"}}
}
```

### Citation prompt calibration

`CITATION_PROMPT` gains rule #7 (search-result facts delivered through
`tool_result` are the most important to cite, with the
`[url: … | title: …]` metadata shape the model actually sees) and a second
worked example (assembled from several search hits). The prompt stays
server-agnostic — no server names, no vertical enumeration. It remains a
module-level constant so the stable prefix stays prompt-cache safe.

## Testing strategy (revised)

**No flaky visible-marker E2E.** Model adherence to the `【N-M】` rule is not
reliable across providers, and asserting on the final assistant text turns
the regression signal into noise. The supported regression surface is:

1. **Captured-structure unit tests** (always-on, in CI) —
   `backend/tests/unit/test_search_citation_extraction.py` parameterizes the
   real captured response from each shipping provider through `extract_items`
   + `extract_metadata` + `extract_text`, asserting the configured
   `CitationConfig` correctly surfaces the source `url`/`title` and a
   non-empty snippet. The captured fixtures live in
   `backend/tests/unit/fixtures/search_responses/`. Bocha's shape is included
   so the dotted-path `extract_items` extension is guarded.

2. **Live API smoke tests** (opt-in, `requires_api_key` marker) — the same
   test file hits each provider's REST endpoint and re-validates the
   captured shape against today's response. Skipped when the relevant env
   var is missing; never auto-run in CI; useful as a manual pre-deploy check
   to catch silent provider-side schema drift before a workspace hits it.

3. **Auth-plumbing unit tests** —
   `backend/tests/unit/mcp/test_runtime_static_auth.py` covers
   `_apply_static_credential` for bearer / custom-header / query-param,
   plus the fallback-to-bearer behaviour when the header/param name is
   missing, plus `_inject_query_param` (append / preserve other params /
   replace collision).

4. **Existing catalog-seed unit suite** stays green — the new entries
   round-trip through `CitationConfig(**raw)` (asserted by
   `test_all_seed_tool_citations_are_valid_citation_configs`), and the
   idempotency / deprecation tests stay green.

5. **Prompt-cache E2E gate** (`tests/e2e/memory/test_prompt_cache.py`)
   stays green; the calibrated prompt is still a module-level constant, no
   per-turn or per-workspace dynamic content enters the stable prefix.

## Open Questions

- **OQ-A — Bocha catalog entry.** No official hosted MCP at PR time. The
  REST shape is captured + tested so we're ready when one lands; the
  third-party `mcp.ecn.ai/{CID}/bochaai/mcp` gateway isn't trustworthy
  enough to seed (different operator, no SLA). Revisit when Bocha publishes
  an official hosted MCP.
- **OQ-B — Perplexity catalog entry.** Perplexity CTO publicly moved away
  from MCP at Ask 2026 in favour of their Agent API. There is no durable
  hosted MCP endpoint to point at. Revisit if Perplexity reverses course.
- **OQ-C — News vs web `source_type`.** Tavily news and web share one
  tool (`tavily_search`); `CitationConfig` discriminators key off *result*
  fields, not call args, so we can't split news vs web purely from config.
  v1 labels both `web`. Add a discriminator only if E2E shows a panel-UX
  gap.
- **OQ-D — One shared prompt vs per-source nuance.** Calibration is
  global. Is a single prompt enough for web + academic + code + Chinese,
  or will some vertical want different attribution guidance? Keep global
  for prompt-cache simplicity; revisit only if real-world citation
  adherence shows a per-vertical gap.
- **OQ-E — Default credential policy.** `org` (shared key) matches
  `webtools`, but search keys are metered per query — does a noisy
  workspace burn the org's quota? Consider `workspace` policy for search
  connectors. v1 ships `org` to match the rest of the catalog.

## References

- Tavily MCP — https://docs.tavily.com/documentation/mcp ;
  https://github.com/tavily-ai/tavily-mcp
- Exa MCP — https://exa.ai/docs/reference/exa-mcp ;
  https://github.com/exa-labs/exa-mcp-server
- Jina MCP — https://github.com/jina-ai/MCP ; https://mcp.jina.ai/v1
- Bocha Search MCP — https://github.com/BochaAI/bocha-search-mcp ;
  https://open.bochaai.com
- Perplexity MCP — https://github.com/perplexityai/modelcontextprotocol ;
  Perplexity CTO move-away-from-MCP note: Awesome Agents recap of Ask 2026

Internal references:
- Existing citation design: `docs/dev/specs/2026-05-14-mcp-tool-citations-design.md`
- Citation prompt: `backend/cubeplex/prompts/citations.py`
- Citation config model: `backend/cubeplex/middleware/citations/config.py`
- Citation middleware: `backend/cubeplex/middleware/citation.py`
- Catalog seed: `backend/cubeplex/mcp/template_seed.py`
- Runtime auth dispatch: `backend/cubeplex/mcp/cubepi_runtime.py`
  (`_resolve_auth_from_spec`, `_apply_static_credential`, `_inject_query_param`)
- Captured-shape fixtures:
  `backend/tests/unit/fixtures/search_responses/{tavily,exa,bocha,jina}_search.json`
- Auth-plumbing migration:
  `backend/alembic/versions/49844ea5ac7b_mcp_template_install_static_auth_style_.py`
- Citation columns migration:
  `backend/alembic/versions/94630a9e13b4_add_tool_citations_to_mcp_tables.py`
- Prompt-cache discipline: `backend/docs/prompt-cache-discipline.md`
