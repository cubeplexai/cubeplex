# tests/diagnostic/

> **Archive note:** This directory documents the closed Phase 1/2 prompt-cache
> investigation run during the cubepi migration. References to "langgraph"
> below point at the now-removed dual-runtime path; they are preserved
> verbatim because the comparative findings are the substantive output of
> the investigation. The raw Phase 1 cache smoke tests are still useful as
> standalone provider probes; the Phase 2 runtime-comparison tools are
> historical (one runtime now).

Diagnostic scaffold for the cubepi cache migration investigation.

## Purpose

This directory contains raw HTTP cache validation tests and runtime comparison
tools. The tests help confirm which providers support prompt caching and diagnose
request-shape differences between the langgraph and cubepi runtimes.

## Files

| File | Phase | Purpose |
|---|---|---|
| `_common.py` | Shared | `LONG_SYSTEM_PROMPT`, cache assertion helpers |
| `_capture.py` | 2 | `CapturingAsyncTransport` — httpx transport that writes each request to JSON |
| `test_raw_deepseek_anthropic_cache.py` | 1 | Raw API smoke: deepseek/anthropic (explicit cache_control markers) |
| `test_raw_alicode_chat_completions_cache.py` | 1 | Raw API smoke: alicode/qwen3.6-plus (auto-cache) |
| `test_raw_arkcode_chat_completions_cache.py` | 1 | Raw API smoke: arkcode/doubao-seed-2.0-pro (auto-cache) |
| `test_capture_runtime_requests.py` | 2 | Capture + compare langgraph vs cubepi outbound HTTP bodies |
| `compare_runtimes.py` | 2 | CLI diff tool: field-level JSON diff between two capture directories |

## Phase 1 — Raw cache smoke tests

Bypass cubeplex, cubepi, and langchain entirely. Send two identical prompts directly
to a provider API and check whether the second request reports cache tokens > 0.

**Phase 1 results (2026-05-14):**

| Provider | API style | Raw cache hit? |
|---|---|---|
| deepseek-v4-pro | Anthropic (cache_control markers) | YES — turn 2 cache_read=1920 |
| doubao-seed-2.0-pro (arkcode) | OpenAI Chat Completions (auto-cache) | YES — turn 2 cached_tokens=1848 |
| qwen3.6-plus (alicode) | OpenAI Chat Completions (auto-cache) | NO — provider limitation |

### How to run Phase 1

```bash
# All providers
uv run pytest tests/diagnostic/ -v -m "real_llm and diagnostic" -s

# Single provider
uv run pytest tests/diagnostic/test_raw_deepseek_anthropic_cache.py -v -m real_llm -s
uv run pytest tests/diagnostic/test_raw_arkcode_chat_completions_cache.py -v -m real_llm -s
```

## Phase 2 — Runtime request capture and diff

Capture the exact outbound HTTP request body that each runtime (langgraph vs
cubepi) sends to the provider, then diff them to find divergences that break
auto-caching.

### How to run Phase 2

```bash
# Capture (4 tests: 2 runtimes × 2 providers; writes to /tmp/cubepi_runtime_capture/)
uv run pytest tests/diagnostic/test_capture_runtime_requests.py -v -m real_llm -s

# Diff results
uv run python tests/diagnostic/compare_runtimes.py \
    /tmp/cubepi_runtime_capture/langgraph/deepseek_anthropic \
    /tmp/cubepi_runtime_capture/cubepi/deepseek_anthropic

uv run python tests/diagnostic/compare_runtimes.py \
    /tmp/cubepi_runtime_capture/langgraph/arkcode_openai \
    /tmp/cubepi_runtime_capture/cubepi/arkcode_openai

# Prefix stability (turn1 vs turn2 within cubepi)
uv run python tests/diagnostic/compare_runtimes.py \
    /tmp/cubepi_runtime_capture/cubepi/arkcode_openai \
    /tmp/cubepi_runtime_capture/cubepi/arkcode_openai \
    --files openai_001.json openai_002.json

# Summary of all captures
uv run python tests/diagnostic/compare_runtimes.py --summary
```

### Phase 2 findings (2026-05-14)

**deepseek/anthropic diffs (langgraph vs cubepi):**
- `body.system`: langgraph sends a plain string; cubepi sends a list block with `cache_control: ephemeral`.
  cubepi actually sends the BETTER shape here (with cache markers).
- `body.messages[0].content`: same format difference.
- `body.temperature`: langgraph sends `0.7`; cubepi omits it.

**arkcode/openai diffs (langgraph vs cubepi) — ROOT CAUSE found:**
- `body.messages[0].content` (system message): langgraph sends `"<plain string>"`;
  cubepi was sending `[{"type": "text", "text": "..."}]` (list of blocks).
  OpenAI auto-cache hashes raw bytes — different format = different cache bucket = MISS.
- `body.stream`: langgraph non-streaming (`false`); cubepi streaming (`true`). Does NOT affect cache key.
- `body.stream_options`: cubepi includes `include_usage`; langgraph omits. Does NOT affect cache key.

**Fix applied:** `cubepi/providers/openai.py` line 71 — changed system message content from
`[{"type": "text", "text": system_prompt}]` to the plain string `system_prompt`.

**Result:** `tests/e2e/memory/test_prompt_cache.py` PASSES under cubepi runtime with arkcode/doubao-seed-2.0-pro.

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
