# Admin Trace Viewer

A trace browsing UI inside the org-admin console that reads from the same
Grafana Tempo instance cubebox already exports OTLP spans to. Org admins
can filter their org's traces by workspace, user, conversation, run id,
model, and time window, drill into a span tree, and inspect LLM
request/response payloads — without leaving cubebox or learning TraceQL.

The reference implementation `~/cubetrace` (Traceloop-shape + Elasticsearch)
is **design reference only** — its layout, span-row treatment, and
collapsible LLM cards are worth lifting; the code is not.

---

## Why

Tempo's Grafana panel cannot filter by cubebox business identifiers in a
usable way — TraceQL works, but `{ span.cubepi.metadata.workspace_id="ws-…" }`
is not a UX an org admin will type. Meanwhile every cubebox trace already
carries the right attributes (`cubepi.metadata.{org_id, workspace_id,
user_id, conversation_id}`, `cubepi.run_id`, full `cubepi.llm.raw_request`
and `raw_response`), so a thin admin UI in front of Tempo is enough.

---

## Scope

In:

- Org-admin trace **list** with filters: workspace, user, conversation,
  run id, model, time window, duration. Sort by start time / duration.
- Trace **detail**: span tree on the left, span detail on the right.
- LLM-specific rendering: model + params, token breakdown (incl. cache
  reads), tools defined, full conversation messages, raw request/response,
  events, errors.
- Read-only — no edits, no replay.

Out (deferred):

- Model debugger / replay (the `ModelDebugger` page in `~/cubetrace`).
  When we want it, it can land as a separate `/admin/debug` route.
- Cross-org / system-admin views.
- Linking back to Grafana / Loki — single-system UX first.

---

## Permission Model

- Routes live under `/api/v1/admin/traces/...`. Same auth/CSRF as other
  `admin_*.py` routers; require an org-admin role.
- The acting user's `org_id` is taken from the session — never from a
  query param.
- Every TraceQL we send to Tempo is wrapped in
  `{ resource.service.name="cubebox" && span.cubepi.metadata.org_id="<session_org>" && (…user filters…) }`.
- Every detail response is double-checked server-side: if any span in the
  returned trace has a `cubepi.metadata.org_id` attribute that does not
  match the session org, return `404`. (Defence in depth — TraceQL is
  the primary gate, this catches a misconfigured exporter.)
- Traces with **no** `cubepi.metadata.org_id` attribute are invisible to
  admin trace viewer regardless of who is logged in — they're either
  pre-instrumentation legacy spans or non-cubebox services that happen to
  share the Tempo tenant.

---

## Data Source

Read directly from Tempo's HTTP query API (Tempo HTTP port, typically
`3200`). No new database table, no indexing job — Tempo's TraceQL is
already capable of filtering by every business attribute we need.

Config keys (new, under existing `tracing:` block):

```yaml
tracing:
  # existing write-path config: otlp.endpoint etc.
  tempo:
    query_endpoint: "http://localhost:3200"  # null → admin trace UI disabled
    timeout_seconds: 10
```

When `tempo.query_endpoint` is null, the admin trace routes return `503`
and the frontend page renders an empty-state explaining the feature is
not configured for this deployment.

---

## Backend

### Files

- `backend/cubebox/api/routes/v1/admin_traces.py` — three routes (below).
- `backend/cubebox/services/tempo_client.py` — async httpx client that
  wraps Tempo's `/api/search`, `/api/search/tag/{name}/values`, and
  `/api/traces/{id}`. Builds TraceQL strings, parses OTLP JSON into the
  pydantic models the route returns. **This file is the schema mapping**
  — there is no separate mapping doc.
- `backend/cubebox/schemas/trace.py` — pydantic response models
  (`TraceSummary`, `TraceDetail`, `SpanNode`, `LlmCallPayload`, …).

### Routes

All under `/api/v1/admin/traces`, all require org-admin.

`GET /` — list. Query params: `workspace_id`, `user_id`, `conversation_id`,
`run_id`, `model`, `start`, `end` (RFC3339), `min_duration_ms`,
`max_duration_ms`, `limit` (≤100). Returns `{traces: TraceSummary[]}`.
Tempo orders by start time descending and has no native cursor; we cap
the page at `limit` and document the cap rather than pretend to paginate.
Sort-by-duration is a follow-up — it requires either pulling a larger
window and sorting server-side or waiting for Tempo's sort support to
stabilise.

Every filter value is escape-checked before being embedded in the
TraceQL — values containing `"`, `\`, newline, or NUL are rejected with
a 400. cubebox business identifiers are well-formed slugs that never
contain these characters; rejecting is safer than backslash-escaping
because it leaves no ambiguity at the parser layer.

`GET /tag-values?tag=<name>` — autocomplete. Whitelist of allowed tags:
`cubepi.metadata.workspace_id`, `cubepi.metadata.user_id`,
`cubepi.metadata.conversation_id`, `gen_ai.request.model`. Returns
`{values: string[]}`. Tempo's v1 endpoint returns the full value list
scoped by org; the frontend narrows by prefix client-side (a server-side
`filter=` was silently ignored in testing).

`GET /{trace_id}` — detail. Returns `TraceDetail` = trace summary +
hierarchical `SpanNode` tree. Each LLM span carries `LlmCallPayload`
(model, params, token usage, tools, messages, raw_request, raw_response,
finish_reasons, time_to_first_chunk). Each tool span carries
`ToolCallPayload` (name, description, arguments, result, is_error).

### Span taxonomy

cubepi emits four span shapes; the response collapses them to a single
discriminated `SpanNode.kind`:

| Tempo span | `kind` in API | What renders on the right |
|---|---|---|
| `invoke_agent` | `agent` | Run metadata, agent system prompt sha, input/output message counts |
| `cubepi.turn` | `turn` | Turn index, stop_reason, tool_call count |
| `chat <model>` | `chat` | `LlmCallPayload` — the main LLM detail view |
| `execute_tool execute` | `tool` | `ToolCallPayload` |
| anything else | `other` | Name + attributes table only |

---

## Frontend

New route: `frontend/packages/web/app/admin/traces/`.

- `page.tsx` — `TraceListPage`. Filter toolbar (workspace / user /
  conversation / model / time range / duration), virtualized table.
  Row click → push `/admin/traces/{trace_id}`.
- `[traceId]/page.tsx` — `TraceDetailPage`. Two-column shell:
  - Left: `<SpanTree>` — collapsible tree, each row = name + kind chip
    + duration bar (the bar is the cubetrace touch worth keeping).
  - Right: `<SpanDetail>` — collapsible cards. For `chat` spans:
    Model, Token Usage (incl. cache_read), Tools Defined, Messages,
    Raw Request, Raw Response, Events, Performance. For `tool` spans:
    Tool Info, Arguments, Result, Error.

Modules (`SpanTree`, `SpanDetail`, `LlmCard`, `ToolCard`, `JsonBlock`)
live under `app/admin/traces/_components/`; the two pages are pure
assemblies of these modules.

Filter values are kept in the URL query string so a deep-link is the
full filter state. The detail page also accepts `?span=<span_id>` to
preselect a span in the tree.

---

## Not in scope / explicit non-goals

- No copy of any `~/cubetrace` source file.
- No "compatibility shim" that emits Traceloop attributes — cubepi's
  semantic conventions stay as-is.
- No background sync from Tempo into Postgres. If Tempo query latency
  becomes a problem, the answer is paging / caching at the route layer,
  not duplicating storage.
- No write-path changes — `cubepi`'s tracer and the `tracing.otlp.endpoint`
  config stay exactly as they are.

---

## Open questions

- **Single-tenant deployments**: there's only one org, so the org-id
  predicate is trivially satisfied. The page should still render — no
  special-casing needed beyond what the auth layer already does.
- **Trace retention**: Tempo's local-dev config retains some days of
  blocks; production retention is whoever runs the Tempo we point at.
  The UI just says "no traces in window" when nothing matches —
  retention is a Tempo concern, not ours.
