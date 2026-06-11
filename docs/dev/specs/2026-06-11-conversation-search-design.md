# Conversation Search — Design

**Status:** Draft
**Date:** 2026-06-11
**Owner:** xfgong

## 1. Goal

Add a search field at the top of the left sidebar that lets a user find their
own conversations inside the current workspace by typing either a keyword
("docling", "OAuth error") or a natural-language phrase ("the time we talked
about document parsing"). Results show conversation title, a highlighted
snippet, and a click target that jumps to the conversation page (and scrolls
to the matching message when applicable).

The retrieval engine is a **hybrid of lexical (BM25-class) and semantic
(vector) search**, fused with Reciprocal Rank Fusion. The lexical leg is
**pluggable** behind a small abstraction so the choice of Postgres extension
can change between deployments without touching the rest of the system.

## 2. UX

The search field renders inside a popover anchored on a search button in the
sidebar header (the popover opens above the existing nav, does not displace
the recent-conversations list). Opening the popover focuses the input. As the
user types, after a 250ms debounce, the popover body shows a result list:

```
[ title of conversation ]
… snippet text with the matched span highlighted …
                                             3d ago
```

* Up to 8 results.
* Snippet is one chunk's worth of text (≈ a sentence or two), with the match
  offsets returned by the backend so the frontend wraps the matched span(s)
  in `<mark>`. No HTML from the server.
* No results → a single muted "No matches" line. Empty query → popover shows
  a faint hint and no results.
* Clicking a result navigates to
  `/w/{wsId}/conversations/{conversationId}#msg-{matchedMessageSeq}`; the
  conversation page already supports scrolling to a message anchor.
* Escape or click-outside closes the popover. The popover keeps its query
  text across opens within the same page load (cheap UX win, no persistence).

Search is available to every workspace member; no role gate.

## 3. Out of scope

* Cross-workspace search.
* Searching other users' conversations (workspace admins see only their own).
* Searching tool-call raw JSON, attachment binary content, or artifact files.
* RAG-style "answer my question using past conversations". This spec is for
  navigation only.
* Saved searches, filters by date / model / preset.
* Pagination beyond the first 8 results. (Future: "show more".)

## 4. Architecture overview

```
GET /api/v1/ws/{ws}/conversations/search?q=…
                 │
                 ▼
         ┌──────────────────┐
         │  ConvSearchSvc   │
         └────┬─────────┬───┘
              │         │
        ┌─────┘         └──────┐
        ▼                      ▼
  LexicalSearchBackend    VectorSearchBackend
  (PGroonga | pg_bigm)    (pgvector + HNSW)
        │                      │
        ▼                      ▼
    ranked rows            ranked rows
        │                      │
        └───────► RRF ◄────────┘
                  │
                  ▼
       per-conversation aggregation
                  │
                  ▼
              top-K results
```

* All search state lives in **cubebox's Postgres database**. `cubepi` and its
  `cubepi_messages` table are read but never modified. cubepi's contract
  stays "checkpointer for serialized agent state" — search is a product
  concern, not a framework concern.
* The system maintains a per-chunk index table (`conversation_chunks`) whose
  rows mirror windows of the message stream. cubepi_messages remains the
  source of truth; chunks are derived state and can be rebuilt at any time.
* Both retrieval legs hit the same table — different indexes on the same
  rows — so a single `WHERE` scope predicate covers both paths.

## 5. Data model

### 5.1 `conversation_chunks`

| Column | Type | Notes |
|---|---|---|
| `id` | `text PK` | public id, prefix `cck_` |
| `org_id` | `text NOT NULL` | from `OrgScopedMixin` |
| `workspace_id` | `text NOT NULL` | scope |
| `creator_user_id` | `text NOT NULL` | scope (= `Conversation.creator_user_id`) |
| `conversation_id` | `text NOT NULL FK conversations(id)` | cascading delete via app-level soft-delete (mirrors `Conversation`) |
| `chunk_seq` | `int NOT NULL` | 0-based, per conversation |
| `seq_lo` | `bigint NOT NULL` | inclusive lower `cubepi_messages.seq` |
| `seq_hi` | `bigint NOT NULL` | inclusive upper `cubepi_messages.seq` |
| `text` | `text NOT NULL` | the chunk's readable content, what both indexes index |
| `embedding` | `vector(1024) NOT NULL` | Qwen3-Embedding-0.6B default dim |
| `embed_model` | `text NOT NULL` | e.g. `qwen3-embedding-0.6b@dashscope`; lets a future model swap reindex selectively |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | tz-aware |
| `updated_at` | `timestamptz NOT NULL DEFAULT now()` | tz-aware |

Constraints:

* `UNIQUE (conversation_id, chunk_seq)` — rebuild idempotency.
* All FK columns and scope columns NOT NULL; queries always filter on the
  full `(org_id, workspace_id, creator_user_id)` triple before any index
  lookup.

Indexes:

* `ix_chunks_scope` on `(org_id, workspace_id, creator_user_id)` — B-tree;
  the prefilter on every query.
* `ix_chunks_conversation` on `(conversation_id, chunk_seq)` — backfill /
  rebuild / per-conversation reads.
* `ix_chunks_embedding_hnsw` HNSW on `embedding` (pgvector) — semantic leg.
* `ix_chunks_text_lexical` — DDL chosen by `LexicalSearchBackend` at
  migration time (see §6).

Soft-delete: when a conversation is soft-deleted (`deleted_at` is set on
`conversations`), its chunks remain in the table; the search query joins
`conversations` and filters `deleted_at IS NULL`. A separate GC job (out of
scope here) eventually deletes both.

### 5.2 `embedding_jobs`

A Postgres-table-backed work queue. Reason: cubebox already has Redis but
Postgres-only jobs are easier to reason about across restarts and worktrees;
the throughput here is not Redis-stream-class. One small consumer process
(or background task in the existing FastAPI app) drains it.

| Column | Type | Notes |
|---|---|---|
| `id` | `text PK` | prefix `ejob_` |
| `org_id`, `workspace_id`, `creator_user_id` | `text NOT NULL` | scope |
| `conversation_id` | `text NOT NULL` | |
| `seq_lo` | `bigint NOT NULL` | range to (re)index |
| `seq_hi` | `bigint NOT NULL` | inclusive |
| `state` | `text NOT NULL` | `pending` / `running` / `done` / `dead` |
| `attempts` | `int NOT NULL DEFAULT 0` | |
| `last_error` | `text NULL` | |
| `created_at`, `updated_at`, `claimed_at` | `timestamptz` | |

* Indexes: `(state, created_at)` for the claim query; `(conversation_id)`
  for dedup.
* Claim: `UPDATE … SET state='running', claimed_at=now() WHERE id IN (
  SELECT id FROM embedding_jobs WHERE state='pending' ORDER BY created_at
  LIMIT $batch FOR UPDATE SKIP LOCKED) RETURNING …`.
* Retry budget: 5 attempts with exponential backoff (1m, 5m, 25m, 2h, 10h);
  on exhaustion → `dead`, surfaces in a future admin queue page.

## 6. Lexical backend abstraction

```python
class LexicalSearchBackend(Protocol):
    name: ClassVar[str]                              # config key
    index_name: ClassVar[str]                        # used in DDL & migration

    def create_index_sql(self, table: str, col: str) -> str: ...
    def drop_index_sql(self, table: str) -> str: ...

    def search_sql(self) -> str:
        """Parameterised SQL fragment returning (id, score)."""

    def normalize_query(self, q: str) -> str:
        """Escape / quote user input for this backend's query language."""
```

Concrete implementations live under `cubebox/search/lexical/`:

* `PgroongaBackend` (default in self-hosted)
  * DDL: `CREATE INDEX … USING pgroonga (text)`
  * Where: `text &@~ $q`, score from `pgroonga_score(tableoid, ctid)`
  * `normalize_query`: pgroonga-specific reserved-char escape; supports
    boolean / phrase syntax passthrough.
* `PgBigmBackend` (deploy target: AWS RDS / Aurora)
  * DDL: `CREATE INDEX … USING gin (text gin_bigm_ops)`
  * Where: `text LIKE '%' || $q || '%'`; score from
    `bigm_similarity(text, $q)`
  * `normalize_query`: SQL LIKE wildcard / underscore escape only.

Selection: `search.lexical.backend` config key, default `pgroonga`.
Alembic migration reads the same config and chooses which `CREATE INDEX` to
emit. **Only one lexical index exists per deployment**; switching backends
requires a manual reindex migration (acceptable — backend changes coincide
with deployment-target changes, which are rare and planned).

The abstraction touches:

* `cubebox/search/lexical/` (~150 lines total across base + 2 backends)
* one branch inside the alembic migration that creates the chunks table
* one `Depends`/factory in the search service

It does **not** touch: the chunks schema, embedding pipeline, vector query,
RRF fusion, API route, or frontend.

## 7. Embedding provider

One implementation: an OpenAI-protocol HTTP client. Configurable to talk to:

* Aliyun DashScope (`https://dashscope.aliyuncs.com/compatible-mode/v1`),
  model `qwen3-embedding-0.6b` — default for hosted deployments.
* OpenAI (`https://api.openai.com/v1`), model `text-embedding-3-small` —
  fallback choice.
* Local vLLM / Ollama OpenAI-compatible endpoint (`http://localhost:8081/v1`),
  model name as served — for fully air-gapped self-hosted.

Config block (`config.yaml` / overridable per env):

```yaml
search:
  embedding:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env: "DASHSCOPE_API_KEY"
    model: "qwen3-embedding-0.6b"
    dimensions: 1024
    batch_size: 32
    timeout_seconds: 30
```

Interface:

```python
class EmbeddingProvider(Protocol):
    dimensions: int
    model_id: str                   # written to chunks.embed_model

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

The provider writes `model_id = f"{model}@{provider_host_tag}"` to each
chunk row, so when the deployment switches models, a backfill script can
target only rows that don't match the current model id.

The dimension is fixed at deploy time. Changing dimension requires
recreating the `embedding` column (DDL) and reindexing all rows — this is
treated as a planned migration, not a hot swap.

## 8. Indexing pipeline

### 8.1 Chunker

Chunk size **600 tokens**, overlap **100 tokens**, measured by `tiktoken`
`cl100k_base` (close enough across the models we target; precise
tokenisation only matters at the embedder, which has its own tokenizer).

Each chunk's `text` is built from `cubepi_messages` rows in `(seq_lo,
seq_hi)`:

* User messages: their text content, prefixed with `[user]`.
* Assistant messages: visible text content (no reasoning blocks), prefixed
  with `[assistant]`.
* Tool result messages: human-readable text fields only (best-effort
  extraction via the same `text_for_search` helper that the share-page
  rendering uses, when available; otherwise the message is skipped).
* Tool call messages: skipped (their args are not search-worthy for
  navigation).

`seq_hi` lands on a message boundary, never mid-message. Empty chunks (e.g.,
a run that only emitted tool calls) are dropped.

### 8.2 Incremental indexing

After a run finishes (the existing post-stream persistence hook in
`conversations.py:_update_conversation_timestamp`), enqueue one
`embedding_jobs` row covering the seq range added by the run. The hook
already runs on a dedicated NullPool session; the enqueue is one INSERT.

A background worker (a single asyncio task started by the FastAPI lifespan)
drains the queue: claims a batch, loads the cubepi messages, runs the
chunker, calls `EmbeddingProvider.embed` once per batch, writes the rows,
marks the jobs `done`. Failures bump `attempts` and reschedule with backoff.

The worker is bounded:

* One concurrent batch per worker process.
* `embed` batch size from config (default 32 chunks).
* Slow path (failure) does not block fast path (new jobs continue arriving).

### 8.3 Backfill

`backend/scripts/dev/backfill_search_index.py`:

* Resumable: writes progress to a small `search_backfill_progress` table
  keyed by `(workspace_id, conversation_id)` so re-runs skip done work.
* Walks every non-deleted conversation, computes chunks, enqueues
  `embedding_jobs` rows in `pending` state for the worker to pick up — does
  not embed directly. This keeps one code path (the worker) responsible for
  all writes.
* Rate-limited via config (default 5 jobs / sec enqueue, since the embedder
  bottlenecks downstream anyway).

## 9. Query path

`GET /api/v1/ws/{workspace_id}/conversations/search`

Query params:

* `q`: required, 1–200 chars after trim. Reject empty / too-long with 400.
* `limit`: 1–20, default 8.

Response:

```json
{
  "results": [
    {
      "conversation_id": "conv_xxx",
      "title": "Setting up docling parser",
      "snippet": "we ended up using docling because pypdf miscut the tables",
      "match_offsets": [[14, 21]],
      "matched_message_seq": 17,
      "matched_at": "2026-06-09T03:21:04+00:00",
      "score": 0.81
    }
  ],
  "lexical_count": 14,
  "vector_count": 18,
  "fused_count": 23
}
```

### 9.1 Inside the service

1. Validate + normalise `q`. Reject obviously non-textual input (control
   chars).
2. **Run both legs concurrently** (`asyncio.gather`):
   * Lexical: `LexicalSearchBackend.search_sql()` parameterised with
     `(org_id, workspace_id, creator_user_id, normalize_query(q))`, LIMIT 20.
   * Vector: `EmbeddingProvider.embed([q])` → pgvector ANN with the same
     scope predicate, LIMIT 20. Cosine distance, ordered ascending.
3. **RRF fuse** the two ranked lists: `rrf_score(rank) = 1 / (k + rank)` with
   `k = 60` (industry standard, gentle decay). Sum per chunk.
4. **Aggregate to conversation**: group fused chunks by `conversation_id`,
   keep the highest-scoring chunk per conversation as the snippet source.
5. **Compute snippet + offsets**:
   * Find the first occurrence of `q` (case-insensitive, NFC-normalised) in
     the chunk's `text`; if found, centre a window of ~160 chars on it and
     emit `[start, end]` offsets relative to the snippet.
   * If no literal match (semantic-only hit), use the first ~160 chars of
     the chunk and return an empty `match_offsets`.
6. Resolve `matched_message_seq`: pick the `cubepi_messages.seq` containing
   the first matched character offset within the chunk's range — this needs
   a lightweight scan of the chunk's source rows; do it once per result.
7. Truncate to `limit`, return.

Total wall-clock budget: < 500 ms on the 99th percentile. ANN + lexical
queries both hit small (≤ chunk count per workspace) tables behind scope
prefilter; embedding call is the dominant tail.

### 9.2 Scope safety

Both legs filter on `(org_id, workspace_id, creator_user_id)` **before** any
ranking. `ScopedRepository` enforces `(org_id, workspace_id)` structurally
for any repo backed by `OrgScopedMixin`; the additional `creator_user_id`
predicate is an explicit `WHERE creator_user_id = $current_user` clause
added by the search service (chunks carry `creator_user_id` denormalised
from `Conversation` so the search query is one join shorter). There is no
admin override; if an admin wants to search a member's conversations, that's
a separate (out-of-scope) admin route, not a parameter on this one — per
cubebox's scope-isolated APIs rule.

## 10. Frontend

### 10.1 New files

* `components/sidebar/ConversationSearch.tsx` — the popover + input +
  results list.
* `components/sidebar/SearchResultRow.tsx` — single result rendering with
  `<mark>` highlight.
* Hook into the sidebar header: a search icon button next to the brand /
  new-chat button; pressing it (or `⌘K` / `Ctrl+K`) opens the popover.

### 10.2 Behaviour

* Debounced query (250 ms). Cancels in-flight fetch on new keystroke
  (AbortController).
* `match_offsets` from the API are character offsets into `snippet`; the
  component splits `snippet` at the offsets and wraps the matched spans in
  `<mark>`. No `dangerouslySetInnerHTML`.
* On enter / click: navigate via Next.js `router.push(
  `/w/${ws}/conversations/${id}#msg-${matchedMessageSeq}`)` and close the
  popover.
* Failure path: backend 5xx → show "Search unavailable" in muted text;
  embedding-provider error → backend returns lexical-only results, the
  frontend doesn't need to know.
* Empty workspace (no chunks yet) → same "No matches" line; backfill is an
  operator concern, surfaced separately in admin.

### 10.3 Keyboard

* `⌘K` / `Ctrl+K`: open popover, focus input.
* `↑ / ↓`: move highlight in results.
* `Enter`: navigate to highlighted result.
* `Esc`: close popover.

These bindings are local to the popover when open; outside, only `⌘K` /
`Ctrl+K` registers, and only when no other input has focus.

## 11. Testing

### 11.1 Unit

* RRF fuse function (rank → score, ties, dedup).
* Chunker (boundary handling, overlap, skip-empty, message-type filtering).
* Each `LexicalSearchBackend.normalize_query` (escape table).
* Snippet + offset extraction.

### 11.2 E2E (Playwright)

A new spec under `frontend/packages/web/__tests__/e2e/conversation-search/`.
Fixture: three seeded conversations per test user with controlled content
(English-only, Chinese-only, mixed, plus tool-call noise). Cases:

* Exact keyword: typing `docling` → fixture A first.
* Chinese keyword: `文档解析` → same fixture.
* Cross-lingual semantic: `parse documents` → still fixture A despite no
  exact match.
* No matches.
* Navigation: clicking a result lands on the conversation page at the right
  message anchor.

### 11.3 Backend integration (pytest)

* End-to-end indexing: seed messages → enqueue job → drive the worker
  inline → assert chunks exist and embeddings have the right dimension.
* Search API: scope isolation (user A cannot see user B's chunks);
  rejection of bad `q`; lexical-only fallback when embedding provider raises.

### 11.4 CI

* Unit + backend integration on every PR.
* E2E in CI: yes, gated on the embedding-provider environment variable. CI
  runs with a `DASHSCOPE_API_KEY` secret (or a self-hosted local
  OpenAI-compatible endpoint exposed to the runner); when neither is
  available, the E2E suite is skipped with a clear message rather than a
  silent pass.

## 12. Configuration

```yaml
search:
  enabled: true                              # master switch
  lexical:
    backend: "pgroonga"                      # | "pg_bigm"
  embedding:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env: "DASHSCOPE_API_KEY"
    model: "qwen3-embedding-0.6b"
    dimensions: 1024
    batch_size: 32
    timeout_seconds: 30
  chunker:
    target_tokens: 600
    overlap_tokens: 100
  worker:
    poll_interval_seconds: 2
    max_attempts: 5
    backoff_seconds: [60, 300, 1500, 7200, 36000]
  rrf:
    k: 60
    prefetch_per_leg: 20
```

The `lexical.backend` key drives both the runtime backend selection and the
alembic migration that creates the lexical index. Changing it post-deploy
requires running a migration that drops the old index and creates the new
one — surfaced as an operator runbook entry, not an in-process switch.

## 13. Rollout

Sequence:

1. **Migration A**: create `conversation_chunks`, `embedding_jobs`,
   `search_backfill_progress`. Create the lexical index per
   `search.lexical.backend`. Add the HNSW vector index.
2. **Service code**: ship the indexing worker (disabled by config flag) and
   the search route (returns empty results when disabled). Land both in one
   PR so the wiring is reviewed together.
3. **Enable the worker** (`search.enabled = true`) in a dev / staging env.
   Run the backfill script over the seed dataset. Validate end-to-end via
   the E2E suite.
4. **Frontend**: ship the popover behind a build-time flag if needed; once
   the backend is producing useful results, flip on for everyone.
5. **Production**: enable, run backfill, monitor `embedding_jobs.dead` count
   and search latency. No staged rollout per user — search is read-only,
   reversion is "disable the popover".

## 14. Open items

* **Cost ceiling**: Qwen3-Embedding on DashScope is roughly ¥0.07 / 1M
  tokens (verify current pricing at deploy time). The backfill cost
  estimate at first deploy is the only large number; record it before
  enabling.
* **Reindex on model change**: when changing `embedding.model`, decide
  whether to dual-write to a new column during transition or accept a
  blackout window. Captured as a future runbook, not in this design.
* **Cross-user admin search**: out of scope; if needed, will be a separate
  admin route per the scope-isolated APIs convention, not a parameter here.
* **Tool-result text extraction**: relies on `text_for_search` helper that
  the share-page rendering already implies; if that helper does not exist
  yet, this design will need to spec it. Verify before the plan stage.

---

## Appendix A — Why not put the index in cubepi

cubepi owns serialised agent state; semantic search of human-readable text
is a product concern that doesn't generalise to every cubepi downstream.
Three concrete reasons to keep this out of cubepi:

1. **Choice of embedding model is a product decision**, not a framework
   one. Different cubepi users would want different embedders.
2. **Lexical extension choice is a deployment decision** (managed vs
   self-hosted PG). Pushing it into cubepi exports that decision upstream.
3. **cubebox already has the hooks** (run-done) and the DB scope columns
   needed to index without cubepi modifications.

The one upstream change worth considering later is a small `on_messages_
appended(thread_id, seq_range)` callback on the checkpointer so cubebox
doesn't have to compute the new seq range from run boundaries. That's an
optimisation; this design works without it.

## Appendix B — Why not Elasticsearch / Meilisearch / Qdrant

Adding a separate search service buys nothing here:

* The corpus per workspace is small (thousands of chunks, not millions).
* Operational surface explodes (one more thing to back up, version, scale,
  permission, replicate, and secure across workspaces).
* Postgres with pgvector + a lexical extension handles this comfortably,
  with one transactional boundary covering chunks, embeddings, jobs, and
  the conversations they reference.

If the corpus grows to tens of millions of chunks per workspace or
cross-workspace search becomes a product, revisit then.
