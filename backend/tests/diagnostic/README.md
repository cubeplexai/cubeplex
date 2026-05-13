# tests/diagnostic/

Diagnostic scaffold for the cubepi cache migration investigation.

## Purpose

This directory contains raw HTTP cache validation tests that bypass cubebox,
cubepi, and langchain entirely. Each test sends two identical prompts directly
to a provider API and checks whether the second request reports cache_read_tokens > 0.

**This is the "test the test" baseline step**: before blaming the cubepi-migration
path for cache misses, we need to confirm that the underlying providers actually
support prompt caching for our intended request shape (system prompt + cache_control
markers or identical prefix bytes).

## Test strategy

- Phase 1 (this directory): raw API smoke test per provider — establishes ground
  truth about provider-level cache support.
- Phase 2 (future): capture and diff the outbound HTTP that LangGraph vs cubepi-
  runtime sends, to locate where the request shape diverges and breaks caching.

## Providers covered

| File | Provider | API style |
|---|---|---|
| test_raw_deepseek_anthropic_cache.py | deepseek (deepseek-v4-pro) | Anthropic (explicit cache_control markers) |
| test_raw_alicode_chat_completions_cache.py | alicode (qwen3.6-plus) | OpenAI Chat Completions (auto-cache) |
| test_raw_arkcode_chat_completions_cache.py | arkcode (doubao-seed-2.0-pro) | OpenAI Chat Completions (auto-cache) |

## How to run

From `backend/`:

```bash
# All providers
uv run pytest tests/diagnostic/ -v -m "real_llm and diagnostic" -s

# Single provider
uv run pytest tests/diagnostic/test_raw_deepseek_anthropic_cache.py -v -m real_llm -s
uv run pytest tests/diagnostic/test_raw_alicode_chat_completions_cache.py -v -m real_llm -s
uv run pytest tests/diagnostic/test_raw_arkcode_chat_completions_cache.py -v -m real_llm -s
```

## Interpreting results

- **PASS**: provider supports cache for this request shape at the raw API level.
  If the cubepi-runtime cache test FAILS, the problem is in request-shape or
  endpoint-handling — fixable at the adapter layer.
- **FAIL**: provider does not cache even at raw API level — provider limitation
  unrelated to cubepi migration.
- **SKIP**: credentials not configured locally; safe in CI.

## Preserved

These files are kept permanently (per explicit user request) as a reusable
regression scaffold for future cache investigations. Do not delete.
