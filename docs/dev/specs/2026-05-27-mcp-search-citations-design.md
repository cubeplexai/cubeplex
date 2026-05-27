# Search MCP Sources + Citation Rules Design

**Date:** 2026-05-27
**Issue:** #148
**Scope:** Backend catalog seed + citation prompt calibration. No new schema,
no new runtime code — this is a configuration/seed feature on top of the
already-shipped MCP tool-citation machinery.
**Status:** Spec — pending implementation plan.

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

This feature does two things and nothing more:

1. Add a set of search MCP connectors to the seed catalog
   (`backend/cubebox/mcp/template_seed.py::CATALOG`), each with
   `tool_citation_defaults` filled in for its search tools.
2. Calibrate the shared citation prompt
   (`backend/cubebox/prompts/citations.py::CITATION_PROMPT`) so the model
   reliably emits visible source attribution for search results — including on
   models (deepseek-v4-flash) that currently drop markers for facts delivered
   via `tool_result`.

## Goals / Non-goals

**Goals**

- Curate 2–4 search MCP connectors covering web / academic / code / news and
  seed them into the catalog with correct transport, auth, and
  `tool_citation_defaults`.
- Reuse the existing `CitationConfig` + `CitationMiddleware` path verbatim. No
  new columns, no new middleware.
- Calibrate `CITATION_PROMPT` so visible source URL/title attribution lands in
  the final answer the user reads, across our supported models.
- Keep the system-prompt stable prefix prompt-cache-safe.

**Non-goals**

- No new citation data model or runtime changes (the
  2026-05-14 spec already shipped the column, middleware, and per-run loader).
- No per-server custom prompt. One shared citation prompt; calibration is
  global. (Open question OQ-3 revisits this.)
- No frontend work. The citation-mapping editor and citation panel already
  exist from the 2026-05-14 spec.
- No self-hosting a search aggregator. We point at hosted remote MCP servers;
  the user supplies their own API key per install.
- No auto-ranking / re-ranking of search results. We pass through whatever the
  server returns.

## Current citation mechanism in cubebox

How a tool's citation behavior is configured today, end to end:

1. **Catalog seed** — `backend/cubebox/mcp/template_seed.py`. Each
   `MCPConnectorTemplateSeedEntry` carries
   `tool_citation_defaults: dict[str, dict]`, keyed by **bare tool name**, with
   each value a JSON-serialized `CitationConfig`. The `webtools` entry
   (lines ~370–405) is the reference: it maps `web_search`
   (`content_field="results"`, `mapping={url,title,snippet→description}`) and
   `web_fetch` (`content_type="text"`, `args_mapping={url:url}`).

2. **Citation config shape** —
   `backend/cubebox/middleware/citations/config.py::CitationConfig`. Fields:
   `content_type` (`"json"|"text"`), `source_type` (free string, e.g. `"web"`),
   `content_field` (JSON path to the result array, or `None` for whole-response),
   `mapping` (citation-metadata-key → result-field; the special `snippet` key
   names the text field that gets chunked), `args_mapping` (fallback from tool
   call args, e.g. pull `url` from the request when the result is raw text),
   `discriminator_field` / `discriminator_values` (filter which result items
   count).

3. **Seed → DB** — `seed_templates()` upserts `tool_citation_defaults` into
   `mcp_connector_templates.tool_citations` via `repo.upsert_by_slug(...)`. The
   column was added in
   `backend/alembic/versions/94630a9e13b4_add_tool_citations_to_mcp_tables.py`
   (also adds the per-install `mcp_servers.tool_citations` column).

4. **Install → per-install override** — installing a catalog connector
   snapshots `tool_citations` onto the new `mcp_servers` row; from there it is
   workspace-editable and decoupled from the catalog (per the 2026-05-14 spec,
   §Install).

5. **Per-run load** — `load_workspace_mcp_tools_for_cubepi` namespaces tool
   names to `{server}__{tool}` and emits a
   `dict[namespaced_name, CitationConfig]` for the middleware.

6. **Runtime** — `backend/cubebox/middleware/citation.py::CitationMiddleware`:
   - `transform_system_prompt` appends `CITATION_PROMPT`
     (`backend/cubebox/prompts/citations.py`) **only when ≥1 citation config is
     registered** — so conversations with no citation-eligible tools pay zero
     prompt-cache cost.
   - `after_tool_call` parses each tool result per its `CitationConfig`, chunks
     the snippet text, assigns session-incrementing `【N-M】` ids, rewrites the
     LLM-visible content to `【N-M】 [url: … | title: …] chunk`, and emits the
     structured citation on the SSE side channel + `details["citations"]`.

So a "search source with citations" is fully expressible as a catalog seed
entry with the right `tool_citation_defaults` — **no code change is required to
make a search connector cite**, only seed data plus prompt calibration.

## Search MCP candidates

All hosted/remote unless noted. "Result schema" = does the tool return
structured URL/title/snippet we can map into `CitationConfig`. Pricing/free-
tier figures change; verify at install time.

| Name | Transport | Auth | Result schema (URL/title?) | Free tier | Citation refs |
|---|---|---|---|---|---|
| **Exa** | Streamable HTTP (`https://mcp.exa.ai/mcp`) | API key in URL query or `x-api-key` header | Clean JSON: `title`, `url`, snippet, optional full text. Tools incl. web search, research/academic, code search. | Generous per-query free plan; add own key to lift rate limits | [1] |
| **Tavily** | Streamable HTTP (`https://mcp.tavily.com/mcp/?tavilyApiKey=…`) | API key in URL query or `Authorization` header | Results carry `title` + `content`; `topic="news"` for news mode. `tavily-extract` for fetch. | Free tier on tavily.com (credit-based) | [2][3] |
| **Brave Search** | Streamable HTTP (community/`dedalus-labs`; official npm is stdio-first) | `BRAVE_API_KEY` / `--brave-api-key` | v2 schema mirrors Brave API: web/news/video result objects with url + title + description | Free Brave Search API tier (rate-limited) | [4][5] |
| **Perplexity** | Streamable HTTP (official `perplexityai/modelcontextprotocol`) | `PERPLEXITY_API_KEY` | Sonar/Search API: prose answer + structured `search_results` (id/url/title) | Paid API; no durable free tier | [6][7] |
| **Bocha / 博查** | Streamable HTTP / stdio | API key from open.bochaai.com | `bocha_web_search`: title, url, summary, site name, publish time; `bocha_ai_search` adds modal cards. Chinese-web coverage. | Paid; check open.bochaai.com | [8] |
| **Kagi** | stdio (community) / Streamable HTTP variants | Kagi API token | Search results with url/title/snippet | Paid (no free tier) | [9] |
| **SearXNG** (self-host) | stdio + Streamable HTTP (`/mcp`) | none / optional basic auth | Metasearch JSON: url, title, content per result | Free (self-hosted) | [9] |

Notes:
- Exa and Tavily are the cleanest fits: hosted, Streamable HTTP, API-key auth,
  JSON results with explicit `title`/`url`, and they cover multiple verticals
  (Exa: web + research + code; Tavily: web + news + extract).
- Brave's *official* server is stdio-first; the Streamable-HTTP story is a
  community fork. Treat as candidate, not v1 default, until a stable hosted
  endpoint is confirmed.
- Perplexity and Kagi are paid-only; good as catalog entries but not the
  zero-cost default.
- Bocha covers Chinese web, where Exa/Tavily/Brave are weak. Strong candidate
  for the China-facing audience.
- SearXNG is the OSS/self-host escape hatch (no per-query bill, no third-party
  data sharing) but requires the user to run it.

## Proposed design

### Which connectors to add (v1)

Add three catalog entries, covering the four verticals via Exa's multi-tool
surface plus dedicated news/Chinese coverage:

1. **Exa** (slug `exa`) — web + academic/research + code search. Hosted,
   Streamable HTTP, API key. Primary default.
2. **Tavily** (slug `tavily`) — web + news + extract. Hosted, Streamable HTTP,
   API key. Complements Exa with a strong news mode.
3. **Bocha / 博查** (slug `bocha`) — Chinese web + AI search. Hosted, API key.
   Covers the gap Exa/Tavily leave for Chinese-language queries.

Each is added to `CATALOG` as an `MCPConnectorTemplateSeedEntry` shaped like
the existing connectors:

- `transport="streamable_http"`.
- `supported_auth_methods=["static"]` with `static_form_schema=_TOKEN_FIELD`
  and a header template. **Caveat:** Exa/Tavily take the key as a URL query
  param, not a bearer header. If `static_auth_header_template` can only express
  a header, either (a) bake the key into the `server_url` query string at
  install time, or (b) confirm both servers also accept
  `Authorization: Bearer`/`x-api-key`. This is OQ-1 — resolve before coding.
- `default_credential_policy="org"` (one org key, shared across the workspace),
  matching `webtools`.
- `tool_citation_defaults` filled per the tool schemas below.

Brave, Perplexity, Kagi, SearXNG are deferred (OQ-2) — documented as future
catalog additions, not v1.

### Citation config per connector

Keyed by the **bare** tool name each server actually exposes (confirm exact
names against each server's tool list at implementation time — these are the
expected shapes):

Exa (`exa`):
```jsonc
{
  "web_search_exa": {
    "content_type": "json", "source_type": "web",
    "content_field": "results",
    "mapping": { "url": "url", "title": "title", "snippet": "text" }
  },
  "research_paper_search_exa": {
    "content_type": "json", "source_type": "academic",
    "content_field": "results",
    "mapping": { "url": "url", "title": "title", "snippet": "text" }
  },
  "code_search_exa": {
    "content_type": "json", "source_type": "code",
    "content_field": "results",
    "mapping": { "url": "url", "title": "title", "snippet": "text" }
  }
}
```

Tavily (`tavily`):
```jsonc
{
  "tavily_search": {
    "content_type": "json", "source_type": "web",
    "content_field": "results",
    "mapping": { "url": "url", "title": "title", "snippet": "content" }
  },
  "tavily_extract": {
    "content_type": "json", "source_type": "web",
    "content_field": "results",
    "mapping": { "url": "url", "snippet": "raw_content" }
  }
}
```
(News is the same `tavily_search` tool with `topic="news"`; we keep
`source_type="web"` unless a discriminator on an args/topic field is warranted —
`CitationConfig` discriminators key off result fields, not call args, so news
vs web split is OQ-4.)

Bocha (`bocha`):
```jsonc
{
  "bocha_web_search": {
    "content_type": "json", "source_type": "web",
    "content_field": "data.webPages.value",
    "mapping": { "url": "url", "title": "name", "snippet": "summary" }
  }
}
```
**Caveat:** `content_field` is a single key lookup in `extract_items`
(`data.get(self.content_field, [])`), not a dotted path. If Bocha nests the
result array (e.g. `data.webPages.value`), the seed must point at whatever
top-level key holds the array, or the server's output must be flattened. This
is OQ-5 — verify Bocha's actual JSON shape and either pick a reachable key or
extend `extract_items` to walk a dotted path (small, isolated change if needed).

### Citation prompt / rule changes

The rules live in **one place**: `backend/cubebox/prompts/citations.py`
(`CITATION_PROMPT`), appended to the system prompt by
`CitationMiddleware.transform_system_prompt`. We **calibrate** it, not fork it.
Calibration goals derived from the deepseek note and the search use case:

1. **Reinforce the "visible answer, not thinking" rule for search.** The
   existing rule #1 already says markers must appear in the answer the user
   reads. deepseek-v4-flash specifically drops `【N-M】` for facts that arrived
   via `tool_result`. Add an explicit line that search-result facts delivered
   through tool results are the most important case to cite, and show the
   url/title metadata the model sees (`[url: … | title: …]`) as the thing it is
   attributing.

2. **Add a worked search example** alongside the existing weather example, so
   the model has a same-shape pattern for "answer assembled from several search
   hits, each fact carrying its marker." The example must use the exact
   `【N-M】` syntax and inline placement.

3. **Keep `source_type` neutral in the prompt.** The prompt should not enumerate
   `web/academic/code/news` — those are metadata, surfaced by the frontend via
   the citation panel, not something the model formats. This keeps the prompt
   stable as we add verticals.

#### Prompt-cache safety

`CITATION_PROMPT` is a module-level constant string. Appending calibration text
keeps it a constant — **no per-turn or per-user dynamic content** enters the
stable prefix, so the discipline in `backend/docs/prompt-cache-discipline.md`
(§Stable Prefix) holds. Two consequences to call out:

- Changing the constant is a one-time content-version bump: every live
  conversation that has citation-eligible tools pays one cache-miss turn at
  deploy, then re-caches. Same class of event as the namespacing bump in the
  2026-05-14 spec; not a discipline violation.
- The prompt is still appended **only when ≥1 citation config is registered**
  (existing `if not self._configs: return system_prompt` guard). Conversations
  without search/citation tools see no prefix change at all.

We must **not** make the prompt conditional on which servers are installed
(e.g. inject server names) — that would make the prefix vary per workspace and
defeat caching. The prompt stays server-agnostic.

#### tool_result-delivered facts (deepseek note)

The middleware already rewrites tool-result content so each chunk is physically
prefixed with `【N-M】 [url: … | title: …]` before the model sees it — the marker
and the source metadata are *in the tool result text*, not just in a side
channel. The failure mode is the model reading those markers, using the fact,
and then not reproducing the marker in its final prose. The prompt calibration
(rule reinforcement + worked search example targeting the visible answer)
directly addresses that. No middleware change; the fix is prompt-level and
verified by E2E (below).

### v1 scope

- Three new catalog seed entries: `exa`, `tavily`, `bocha`, each with
  `tool_citation_defaults`.
- Calibrated `CITATION_PROMPT` (reinforced visible-answer rule + worked search
  example, still server-agnostic and constant).
- Resolve OQ-1 (auth-as-query-param) and OQ-5 (Bocha nested result path) — these
  may require a tiny, isolated helper change but no new subsystem.
- Tests per below.

Explicitly **out** of v1: Brave/Perplexity/Kagi/SearXNG entries, per-vertical
`source_type` splitting beyond what the result schema gives for free, any
frontend change, any re-ranking.

## Testing strategy

E2E-first, since the whole point is "does the model emit visible citations from
real search results."

1. **E2E (primary)** — extend `tests/e2e/test_mcp_tool_citations.py` (or a
   sibling `test_search_citations.py`):
   - Install a seeded search connector (`exa`/`tavily`) into a test workspace;
     assert `mcp_servers.tool_citations` is populated from the catalog default.
   - Run an agent turn that forces a search tool call. Assert the SSE stream
     carries `citation` events **and** the final assistant message text contains
     `【N-M】` markers (regex `【\d+-\d+】`) attached to factual sentences, with
     no trailing "Sources"/"References" list.
   - Run the same on deepseek-v4-flash (the model that drops markers) to
     directly verify the prompt calibration — this is the regression that
     motivated the prompt change. If a real search key isn't available in CI,
     drive it against `webtools` (already seeded) so the prompt change is
     exercised without a third-party key, and keep the real-key Exa/Tavily run
     as a local/manual check.
   - PATCH a citation mapping → next run reflects it (covered by existing spec;
     keep green).

2. **Unit** —
   - `tests/unit/test_template_seed.py` — assert `exa`/`tavily`/`bocha` entries
     exist with non-empty `tool_citation_defaults` of valid `CitationConfig`
     shape (round-trip through `CitationConfig(**v)`).
   - `tests/unit/test_citation_config.py` — if Bocha needs dotted-path
     `content_field`, add a case for `extract_items` walking the path.

3. **Prompt-cache gate** — `tests/e2e/memory/test_prompt_cache.py` must stay
   green. The prompt is a constant; any assertion that string-matches the old
   `CITATION_PROMPT` text gets updated to the calibrated text.

How we verify "visible citations": the assertion is on the **final assistant
message content** (what the user reads), not on the SSE `citation` side channel
— matching the deepseek failure mode where the side channel is fine but the
prose lacks markers.

## Open Questions

- **OQ-1 — Auth as URL query param vs header.** Exa/Tavily take the API key in
  the `server_url` query string (and/or `x-api-key`), but the existing template
  shape models static auth as a header template. Do we bake the key into
  `server_url` at install, confirm a bearer/`x-api-key` path, or extend the
  template to support query-param auth? Resolve before coding.
- **OQ-2 — Which connectors ship in v1.** Spec proposes Exa + Tavily + Bocha.
  Is Bocha worth shipping day one, or defer until there's China-facing demand?
  Do we want a paid option (Perplexity) in v1 for users who already pay?
- **OQ-3 — One shared prompt vs per-source nuance.** Calibration is global. Is a
  single prompt enough for web + academic + code + news + Chinese, or will some
  vertical (e.g. code search) want different attribution guidance? (Keeping it
  global preserves prompt-cache simplicity; revisit only if E2E shows a gap.)
- **OQ-4 — News vs web `source_type`.** Tavily news is the same tool with a
  `topic` arg; `CitationConfig` discriminators filter on *result* fields, not
  call args, so we can't split news vs web purely from config. Acceptable to
  label both `web`, or do we want a news distinction surfaced in the panel?
- **OQ-5 — Bocha nested result path.** `extract_items` does a single-key lookup,
  not a dotted path. Confirm Bocha's JSON shape; if the array is nested, pick a
  reachable key or extend `extract_items` to walk a dotted `content_field`.
- **OQ-6 — Exact tool names.** Seed keys must match each server's bare tool
  names exactly (e.g. `web_search_exa` vs `web_search`). Confirm against each
  server's live tool list before finalizing the seed.
- **OQ-7 — Default credential policy.** `org` (shared key) matches `webtools`,
  but search keys are metered per query — does a noisy workspace burn the org's
  quota? Consider `workspace` policy for search connectors.

## References

[1] Exa MCP — https://docs.exa.ai/reference/exa-mcp ;
    https://github.com/exa-labs/exa-mcp-server
[2] Tavily MCP docs — https://docs.tavily.com/documentation/mcp
[3] Tavily MCP server — https://github.com/tavily-ai/tavily-mcp
[4] Brave Search MCP — https://github.com/brave/brave-search-mcp-server
[5] Brave Search MCP over Streamable HTTP —
    https://github.com/dedalus-labs/brave-search-mcp
[6] Perplexity MCP — https://github.com/perplexityai/modelcontextprotocol ;
    https://docs.perplexity.ai/docs/getting-started/integrations/mcp-server
[7] Perplexity streaming citations —
    https://docs.perplexity.ai/docs/cookbook/articles/streaming-citations/README
[8] Bocha Search MCP — https://github.com/BochaAI/bocha-search-mcp ;
    https://open.bochaai.com
[9] SearXNG MCP — https://github.com/ihor-sokoliuk/mcp-searxng ;
    Kagi/Perplexity/SearXNG roundup —
    https://www.shareuhack.com/en/posts/best-mcp-servers-guide-2026
[10] Citation patterns across ChatGPT / Claude / Perplexity —
    https://medium.com/@aivsrank/how-ai-engines-cite-sources-patterns-across-chatgpt-claude-perplexity-and-sge-8c317777c71d

Internal references:
- Existing citation design: `docs/dev/specs/2026-05-14-mcp-tool-citations-design.md`
- Citation prompt: `backend/cubebox/prompts/citations.py`
- Citation config model: `backend/cubebox/middleware/citations/config.py`
- Citation middleware: `backend/cubebox/middleware/citation.py`
- Catalog seed: `backend/cubebox/mcp/template_seed.py`
- Citation columns migration:
  `backend/alembic/versions/94630a9e13b4_add_tool_citations_to_mcp_tables.py`
- Prompt-cache discipline: `backend/docs/prompt-cache-discipline.md`
