# Embedding subsystem and the vector-dim contract

Conversation search is hybrid: a lexical leg (pgroonga or pg_bigm over the
chunk text) and a vector leg (pgvector HNSW over an embedding column).
This note covers the embedding side — how the vector dim is chosen, why
that number shows up in three places, and what an operator should do when
they want to swap models.

## Why VECTOR_DIM is everywhere

The same dim is referenced in three places at runtime:

1. **Schema** — `conversation_chunks.embedding` is declared `vector(N)`
   in the alembic migration. HNSW indexes require a fixed dim per column,
   so this is baked into the DDL.
2. **Model** — `ConversationChunk.embedding` is `Vector(VECTOR_DIM)`. The
   SQLAlchemy column has to match the DDL or inserts blow up.
3. **Provider** — `EmbeddingProvider.dimensions` is what the actual model
   returns (Qwen3-embedding-0.6b → 1024, OpenAI text-embedding-3-large →
   3072, etc.). Mismatched vectors are silently truncated or rejected by
   pgvector.

Originally `VECTOR_DIM = 1024` was a literal in both (1) and (2). That made
"plug in a different embedding model" a code change. As of #233 both reads
come from `search.embedding.dimensions` via `cubebox.config`, so a fresh
deployment can pick its own dim by editing config alone.

## The three-way check

`cubebox.search.startup._verify_dim_alignment` runs at FastAPI lifespan
startup and fetches:

- `schema_dim` — `format_type(atttypid, atttypmod)` on
  `conversation_chunks.embedding`, parsed with the regex `vector\((\d+)\)`.
  Returns None if the table doesn't exist (migration not run).
- `config_dim` — `config.get("search.embedding.dimensions", 1024)`.
- `provider_dim` — `provider.dimensions`, read off the constructed
  `EmbeddingProvider`. For the OpenAI-protocol client this comes from the
  same config key, but a future provider could probe the model at startup.

What happens on each mismatch:

| Condition | Outcome |
|---|---|
| All three equal | Worker starts, route serves traffic. |
| schema_dim is None | CRITICAL log "has alembic upgrade head been run?"; provider closed; route returns 503. |
| config_dim ≠ schema_dim | CRITICAL log naming all three values + recovery steps; route returns 503. |
| provider_dim ≠ schema_dim | Same — wrong embedding model for this schema. |
| Provider not constructable (no API key) | WARNING log; worker still runs lexical-only (chunks written with embedding=NULL); route returns 200 with lexical results and vector_count=0. |

The point of this check is loud failure at startup, not silent corruption
at insert time. Without it an operator who edits `search.embedding.model`
but forgets to update `dimensions` would end up with a worker that posts
1024-dim vectors into a 1536-dim column on every conversation.

## How to change dim safely

The schema column type is immutable in practice — pgvector doesn't support
`ALTER TYPE vector(1024) -> vector(1536)` on a populated table. So the
recovery path is destructive and the startup log spells it out:

```bash
# 1) Drop the chunks table (queue tables stay).
psql "$DATABASE_URL" -c "DROP TABLE conversation_chunks CASCADE"

# 2) Edit config.
#    search:
#      embedding:
#        model: "text-embedding-3-large"
#        dimensions: 3072

# 3) Re-run the migration. The model + migration both read the new dim
#    from config now, so the recreated table is vector(3072).
alembic upgrade head     # no-op if table still exists; works if dropped

# 4) Backfill — every existing conversation needs new vectors at the new
#    dim. The script enqueues one embedding_job per conversation.
python scripts/dev/backfill_search_index.py
```

The `embed_model` column on `conversation_chunks` is "model@host"; the
search route's vector leg filters by it, so a half-finished rotation
silently returns only the conversations that already have the new vectors
— never a mix.

## Picking an embedding model

Most modern embedding models support Matryoshka representation learning
(MRL): you can ask for a smaller dim and the model truncates+renormalises.
That means a 1024-dim schema is compatible with a wider set of providers
than the model's native dim suggests.

| Native dim | Representative models | MRL down to |
|---|---|---|
| 1024 | Qwen3-Embedding-0.6B, BGE-M3, jina-embeddings-v3, voyage-3, mxbai-embed-large | n/a |
| 1536 | OpenAI text-embedding-3-small | 512, 256 |
| 3072 | OpenAI text-embedding-3-large | 1024, 512, 256 |
| 4096 | Qwen3-Embedding-8B, E5-mistral-7b | 1024, 2048 |

The default is 1024 because it covers the entire modern open-weight
ecosystem at native dim AND lets OpenAI 3-large operators MRL-down without
a schema migration. Higher dims do score modestly better on recall
benchmarks, but the disk + RAM + HNSW build cost grows linearly and the
typical conversation-search workload is dominated by the lexical leg
anyway.

## When search degrades

Decision flow when the service comes up:

```
search.enabled == false        → subsystem inert, route 503.
search.enabled == true
  EmbeddingProvider.from_config raises (no API key)
                               → WARNING log, worker runs lexical-only
                                  (embedding=NULL), route 200 with
                                  lexical results + vector_count=0.
  provider built
    _verify_dim_alignment fails
                               → CRITICAL log with recovery path,
                                  provider closed, worker runs
                                  lexical-only (route 200, vector
                                  leg skipped).
    _verify_dim_alignment passes
                               → lexical backend built, worker started,
                                  route serves traffic.
lexical extension missing      → alembic upgrade fails earlier
                                  (CREATE EXTENSION pgroonga), the API
                                  never boots in the first place.
```

Three failure modes, three different fixes — and only the lexical-extension
case actually stops the API, because at that point we can't even create
the chunks table. Unlike the earlier design, the "no API key" and "dim
mismatch" no longer return 503 — they degrade to lexical-only so users
without an embedding budget still get usable keyword search.

## Lexical-only mode

When `DASHSCOPE_API_KEY` (or the configured provider's key) is unset,
`EmbeddingProvider.from_config()` raises `RuntimeError`. The startup
subsystem catches this and:

- Logs a WARNING (not an error — the operator may have intentionally
  skipped vector search).
- Sets `app.state.embedding_provider = None`.
- `build_lexical_backend()` still runs — PGroonga (or pg_bigm) is
  available.
- The `EmbeddingWorker` is started with `provider=None`.

The worker skeleton runs normally: it ticks, claims jobs, loads messages,
chunks text, and writes `ConversationChunk` rows — but with
`embedding=NULL` and `embed_model=""`. This means the lexical leg
(PGroonga over the chunk text) has data to query even without vectors.

The search route always returns 200 (no 503). The `ConversationSearchService`
is constructed with `provider=None`; `_vector_leg` returns `[]`
immediately (it also filters `WHERE cc.embedding IS NOT NULL` defensively).
RRF receives empty vector results, so the fused ranking is purely the
lexical scores. The response carries `vector_count=0` and
`lexical_count > 0`.

When an operator later configures an API key and restarts:
1. The worker runs with a real provider and writes vector chunks normally.
2. Existing conversations that were indexed in lexical-only mode still
   have rows with `embedding=NULL` and `embed_model=""`.
3. The backfill script (`scripts/dev/backfill_search_index.py`) re-enqueues
   all conversations; re-indexed rows get vectors. Until then, the vector
   leg skips NULL-embedding rows (the `IS NOT NULL` guard) while the
   lexical leg continues to serve them.

To re-embed all NULL chunks in bulk:

```bash
# Re-enqueue every conversation for re-indexing.
python scripts/dev/backfill_search_index.py
```

The `WHERE embed_model = ''` condition identifies chunks that still need
vectors after a provider is configured. A future operator command could
target only these, but the backfill script is idempotent — re-running it
is safe.

## See also

- `backend/cubebox/search/startup.py` — the actual startup wiring.
- `backend/cubebox/search/embedding.py` — `EmbeddingProvider.from_config`.
- `backend/alembic/versions/fabe1279b9f6_conversation_search_tables.py` —
  the migration that builds the `vector(N)` column.
- `docs/dev/specs/2026-06-11-conversation-search-design.md` — the original
  design.
