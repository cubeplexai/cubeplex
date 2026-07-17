# Prompt Cache Discipline

**Read before modifying:** LLM call path, system prompt, tools, memory,
middleware, message replay, or anything that becomes part of the byte stream
sent to the model.

Memory and agent costs depend on prompt-cache stability. The disciplines
below must hold across **all** backend changes that touch the LLM call path.
Breaking them quietly inflates token bills. The cache E2E test
(`tests/e2e/memory/test_prompt_cache.py`) is the regression gate.

---

## Stable Prefix (cache-eligible region)

The byte stream sent before the cache breakpoint must be **byte-identical
across turns of the same conversation.** The stable prefix consists of:

1. Cubeplex base system prompt.
2. Tool definitions in deterministic order. (Toggling MCP tools mid-
   conversation is treated as a new conversation.)
3. Pinned memory (`preference` + `correction` items), sorted by
   `created_at ASC` so additions append to the end.

**Forbidden in the stable prefix:**

- Timestamps, "current time" markers, per-turn nonces, trace ids.
- Random ordering — always use deterministic sort.
- Per-user / per-turn dynamic info that changes between requests.

If the model needs current time, inject it into the **most recent user
message tail** (after the cache breakpoint), not the system prompt.

---

## Per-turn Relevance Memory and the Snapshot Channel

Relevance memory is captured per turn as an immutable `MemorySnapshot` and
stored in the `UserMessage.metadata["memory_snapshot"]` slot of the
persisted message. (cubepi's checkpointer treats message metadata as
immutable per row.)

On replay, `MemoryMiddleware` reads each historical user message's
metadata and prepends the rendered snapshot text during
`transform_context` — so the byte stream of past turns is reproduced
exactly across subsequent requests.

**Do not:**

- Concatenate snapshot text into the persisted user message **content**.
- Re-derive past turns' relevance from the live `MemoryItem` table — the
  snapshot is the source of truth for what the model saw at that turn.
- Reformat assistant messages on replay (whitespace, added metadata,
  reordered tool calls). Send them byte-identical to what the API
  returned.
- Mutate or backfill snapshots when memory items are edited — that
  retroactively contaminates history and breaks cache.

---

## Provider Adapters Own Cache Markers

cubepi's provider adapters know about provider-specific cache mechanics;
cubeplex supplies a `CacheMarkerPolicy` via
`cubeplex/llm/cache_markers.py::CubeplexCacheMarkerPolicy`.

- **Anthropic adapter** (`cubepi.providers.anthropic`): insert
  `cache_control: ephemeral` on the system-prompt boundary and on the
  last completed assistant message (max 4 breakpoints; see Anthropic
  docs). The policy is forwarded via
  `LLMFactory.build_cubepi_provider(..., cache_policy=...)`.
- **OpenAI / OpenAI-compatible** (`cubepi.providers.openai`): no markers
  — auto-cache hits whenever the byte prefix is stable.

Middleware produces a provider-neutral logical request structure;
**inserting `cache_control` anywhere upstream of the cubepi adapter is a
layering violation.**

---

## Why Bake R into the Snapshot, Not Request-Time Only

Cost analysis (see memory system design spec, *Cache decision record*):

Not baking causes a **1-turn cache lag** on auto-caching providers —
every turn pays full price for the prior turn's history. Baking
eliminates the lag at the cost of paying cache-rate for past R. For
agentic workloads (per-turn history in 10⁵–10⁶ tokens, R in 10⁴ tokens),
baking wins until conversations reach tens to hundreds of turns. The
1-turn breakeven argument is wrong; **this is settled — bake.**

---

## When the Cache Test Fails

If `tests/e2e/memory/test_prompt_cache.py` fails after your change:

1. **Do not weaken the bar to make it pass.**
2. Find the dynamic content your change added to the stable prefix or to
   the snapshot replay. Common culprits:
   - Timestamps in system prompt.
   - Non-deterministic tool ordering.
   - JSON dict serialization without `sort_keys=True`.
   - `datetime` objects without canonical UTC formatting.
   - Debug-mode trace ids leaking into prompts.
3. Move that content past the breakpoint, or make it deterministic.
