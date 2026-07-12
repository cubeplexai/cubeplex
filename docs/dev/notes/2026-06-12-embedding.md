# Embedding subsystem, the vector-dim contract, and lexical-only mode

Conversation search is hybrid: a lexical leg (pgroonga or pg_bigm over the
chunk text) and a vector leg (pgvector HNSW over an embedding column).
This note covers the embedding side — how the vector dim is chosen, why
that number shows up in three places, and what an operator should do when
they want to swap models.

## Where things live

The subsystem is `cubeplex.services.conversation_search` (a regular
service package). Tests sit at `backend/tests/services/conversation_search/`
for unit pieces and `backend/tests/e2e/test_conversation_search*` for the
DB-backed paths.

## The two embedding dim knobs

Config splits the dim into two values to remove the old
"is-it-the-column-or-the-API?" ambiguity:

- `search.embedding.vector_dim` — width of the Postgres `embedding`
  column. Frozen at `alembic upgrade head` time. To change later you have
  to drop the table and re-migrate (see "How to change dim" below).
- `search.embedding.dimensions` — `dimensions` parameter sent on
  `/v1/embeddings` requests. `0` means don't send it at all (use the
  model's native dim). Non-zero asks the provider to truncate
  server-side, which OpenAI text-embedding-3-* and most Matryoshka
  models support, but many other models reject.

When `dimensions != 0` it must equal `vector_dim`. `EmbeddingProvider.
from_config` rejects the mismatch up front.

## The enable switch

`search.embedding.enabled` is an explicit on/off:

- `false` (default) — worker still indexes chunks so PGroonga has rows to
  query, but skips embedding. Search runs in lexical-only mode. No
  warnings — this is a normal default.
- `true` — startup tries `EmbeddingProvider.from_config`. Missing
  `api_key` or a non-zero `dimensions` ≠ `vector_dim` raises and the
  subsystem degrades to lexical-only with a WARNING (the operator asked
  for vector search; the config is wrong).

`search.enabled` is the outer kill switch — false here disables the
whole subsystem (no worker, no route data) and is independent of
`embedding.enabled`.

## The three-way check

`cubeplex.services.conversation_search.startup._verify_dim_alignment` runs
at FastAPI lifespan startup, after a provider is built, and fetches:

- `schema_dim` — `format_type(atttypid, atttypmod)` on
  `conversation_chunks.embedding`, parsed with `vector\((\d+)\)`. None
  when the table doesn't exist (migration not run).
- `config_dim` — `config.get("search.embedding.vector_dim", 1024)`.
- `provider_dim` — `provider.vector_dim`, set when the provider was
  built. For the OpenAI-protocol client this comes from config; a future
  provider could probe the model at startup.

What happens on each mismatch:

| Condition | Outcome |
|---|---|
| All three equal | Worker starts with provider, vector + lexical legs serve traffic. |
| schema_dim is None | CRITICAL log "has alembic upgrade head been run?"; provider closed; subsystem degrades to lexical-only. |
| config_dim ≠ schema_dim | CRITICAL log naming all three values + recovery steps; provider closed; lexical-only. |
| provider_dim ≠ schema_dim | Same — wrong embedding model for this schema; lexical-only. |
| `embedding.enabled=false` | INFO log; no provider; lexical-only by design. |
| Provider construction fails (no api_key etc.) with `embedding.enabled=true` | WARNING log; lexical-only. |

The route returns 200 in every "lexical-only" row — search is never
hard-disabled when the schema is healthy.

## How to change vector_dim safely

The schema column type is immutable in practice — pgvector doesn't
support `ALTER TYPE vector(1024) -> vector(1536)` on a populated table.
The recovery path is destructive and the startup log spells it out:

```bash
# 1) Drop the chunks table (queue tables stay).
psql "$DATABASE_URL" -c "DROP TABLE conversation_chunks CASCADE"

# 2) Edit config.
#    search:
#      embedding:
#        enabled: true
#        model: "text-embedding-3-large"
#        vector_dim: 3072
#        dimensions: 0       # use native dim
#        # ...or to keep storage flat:
#        # vector_dim: 1024
#        # dimensions: 1024  # ask OpenAI to truncate to 1024 (MRL)

# 3) Re-run the migration. Both the model and migration read vector_dim
#    from config, so the recreated table is vector(N).
alembic upgrade head

# 4) Backfill — every existing conversation needs new vectors at the new
#    dim. The script enqueues one embedding_job per conversation.
python scripts/dev/backfill_search_index.py
```

The `embed_model` column on `conversation_chunks` is "model@host"; the
search route's vector leg filters by it, so a half-finished rotation
silently returns only the conversations that already have the new
vectors — never a mix.

## Picking an embedding model

Most modern embedding models support Matryoshka representation learning
(MRL): you can ask for a smaller dim and the model truncates+renormalises.
That means a 1024-dim schema is compatible with a wider set of providers
than the model's native dim suggests.

| Native dim | Representative models | MRL down to |
|---|---|---|
| 1024 | Qwen3-Embedding-0.6B, BGE-M3, jina-embeddings-v3, voyage-3, mxbai-embed-large | n/a |
| 1536 | OpenAI text-embedding-3-small | 512, 256 (via `dimensions`) |
| 3072 | OpenAI text-embedding-3-large | 1024, 512, 256 (via `dimensions`) |
| 4096 | Qwen3-Embedding-8B, E5-mistral-7b | 1024, 2048 |

The default is `vector_dim: 1024` because it covers the entire modern
open-weight ecosystem at native dim AND lets OpenAI 3-large operators
MRL-down without a schema migration (set `dimensions: 1024`). Higher
dims do score modestly better on recall benchmarks, but the disk + RAM +
HNSW build cost grows linearly and the typical conversation-search
workload is dominated by the lexical leg anyway.

## Lexical-only mode

When `search.embedding.enabled=false`, or when provider construction
fails, the startup subsystem:

- Logs INFO (disabled by config) or WARNING (enabled but misconfigured).
- Sets `app.state.embedding_provider = None`.
- Still calls `build_lexical_backend()` — PGroonga (or pg_bigm) is
  available.
- Starts `EmbeddingWorker(provider=None)`.

The worker runs normally: it ticks, claims jobs, loads messages, chunks
text, and writes `ConversationChunk` rows — but with `embedding=NULL`
and `embed_model=""`. The lexical leg has data; the vector leg returns
`[]` (and the SQL adds `WHERE cc.embedding IS NOT NULL` defensively).
RRF gets empty vector results, so the fused ranking is purely lexical
scores. The response carries `vector_count=0` and `lexical_count > 0`.

When an operator later flips `embedding.enabled=true` and restarts:

1. The worker runs with a real provider and writes vector chunks for
   new conversations normally.
2. Existing conversations indexed in lexical-only mode still have rows
   with `embedding=NULL` and `embed_model=""`.
3. `scripts/dev/backfill_search_index.py` re-enqueues all conversations;
   re-indexed rows get vectors. Until then, the vector leg skips
   NULL-embedding rows while the lexical leg continues to serve them.

## See also

- `backend/cubeplex/services/conversation_search/startup.py` — startup wiring.
- `backend/cubeplex/services/conversation_search/embedding.py` —
  `EmbeddingProvider.from_config`.
- `backend/alembic/versions/fabe1279b9f6_conversation_search_tables.py` —
  the migration that builds the `vector(N)` column.
- `docs/dev/specs/2026-06-11-conversation-search-design.md` — original design.
