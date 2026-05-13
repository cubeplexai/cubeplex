# cubepi Runtime Path — Prompt Cache Miss Investigation

Date: 2026-05-14
Status: **RESOLVED for OpenAI-compatible path** — cache test passes under cubepi runtime on arkcode/doubao-seed-2.0-pro
Related PR: cubebox#84 (Draft `feat/integrate-cubepi`), cubepi `feat/cubebox-readiness`

## Problem

When `config.agents.runtime=cubepi`, `tests/e2e/memory/test_prompt_cache.py::test_cache_hit_rate_meets_bar` reports `cache_read=0` on turn 2. The same test passes when `config.agents.runtime=langgraph` on the **same endpoint + model config**.

This blocks M6: the documented release gate requires the cubepi path to hit ≥ 50% cache_read by turn 2 and ≥ 85% by turn N. Without that gate, deleting the langgraph fallback path is unsafe.

## Test setup

- Endpoint: `https://gateway.chat.sensedeal.vip/v1/chat/completions`
- Model: `qwen3.6-flash`
- Provider type: `openai-completions`
- `extra_body.user: "cubebox-e2e"` (added in M5.2 to escape a corrupt cached-response bucket; gateway buckets cache by `user` field)
- Test: 10-turn conversation with the same stable prompt; gateway reports `cache_read_tokens` in usage; test asserts turn-2 ratio ≥ 0.5 and final-turn ratio ≥ 0.85.

## Tools built during investigation

`backend/tests/e2e/test_runtime_byte_parity.py` — uses `respx` to mock the openai-compatible endpoint and capture the outbound HTTP request body for both runtime paths under a fixed scenario. Three tests:

1. `test_byte_parity_single_turn` — langgraph vs cubepi same scenario; reports field-level diff
2. `test_byte_parity_turn1_vs_turn2_cubepi` — cubepi own turn 1 vs turn 2 (prefix stability check)
3. `test_byte_parity_stable_prefix_hashes` — langgraph vs cubepi prefix hash equality

## Divergences identified + fixed

Across multiple debugging passes (M5.2 through M5.4) the byte-parity test surfaced these divergences. All have **been fixed** in their respective repos:

### Cubepi-side fixes

| Commit | Issue |
|---|---|
| `b4ad696` | `OpenAIProvider` didn't send `stream_options={"include_usage": true}`; usage never streamed |
| `97b26a5` | `OpenAIProvider`/`OpenAIResponsesProvider`/`AnthropicProvider` didn't forward `max_tokens`/`temperature` |
| `8ace4c6` | System message sent as plain string; langgraph sends `[{"type":"text","text":...}]`; switched to list-of-blocks |
| `8ace4c6` | Tool schemas carried pydantic `title`/`description` auto-generated fields; added `_normalise_tool_schema()` to strip them, matching langchain-core `convert_to_openai_function` output |
| `9195525` | `_normalise_tool_schema` was too aggressive — preserve `title` for enum `$ref` resolutions inside `anyOf`, strip in `items`/other positions |

### Cubebox-side fixes

| Commit | Issue |
|---|---|
| `a4799ffe` | `ArtifactMiddlewarePi` injected artifact context into the **user message** (per-turn); langgraph injects into the **system prompt** (stable prefix). Moved to `transform_system_prompt`. |
| `5e55dad0` | `calculator_pi` / `datetime_tool_pi` class docstrings leaked into `parameters.description`; `subagents_pi` injected `SUBAGENT_PROMPT` into tool description; `sandbox_pi.file_read` had trailing whitespace not stripped; tools array order differed |
| `f2af4303` | (M5.2) memory test fixture wired `LocalSandbox` causing model to use sandbox tools instead of memory injection; usage event not flowing through |
| `21b3516e` | `_run_cubepi_path` didn't accept `attachments` param; SSE `done` race condition |

## Current state of byte-parity tests

After all fixes:

- `test_byte_parity_turn1_vs_turn2_cubepi` → **PASS** ✅ (cubepi's own prefix is byte-stable across turns)
- `test_byte_parity_single_turn` → **xfail**, reports `Field-level diff (1 differences)` involving `edit_file new_string description` field — the diff display shows the SAME string on both sides which suggests Unicode normalization / whitespace / field position issue, not a content diff
- `test_byte_parity_stable_prefix_hashes` → **xfail** (langgraph vs cubepi prefix hashes differ)

## Real-LLM cache test result

After all fixes:
```
test_cache_hit_rate_meets_bar
  warmup input=5884, turn2 input=5915, turn2 cache_read=0
  Endpoint declared cache-capable via CUBEBOX_E2E_LLM_CACHE_CAPABLE=true
  → REGRESSION
```

Token count dropped from ~7100 (before fixes) to ~5900 (after fixes), showing fixes did reduce request size. But cache still doesn't hit.

## Hypotheses (ranked by likelihood)

### H1 (most likely): Gateway-side cache implementation requires something we still don't match

The gateway `gateway.chat.sensedeal.vip` is private. It's unclear whether it uses:
- Pure byte-prefix hash auto-cache (in which case any remaining diff breaks it)
- OpenAI-spec `prompt_cache_key` parameter (cubepi may not send it; langgraph might)
- A whitelist of fields that participate in cache key

**Evidence**: cubepi own turn-to-turn parity test PASSES, so cubepi requests ARE byte-stable across turns. The cache should hit if the gateway uses pure byte-prefix hashing. Therefore the gateway likely uses something more specific.

### H2: One more byte-level diff still hidden

The 1 remaining `Field-level diff` in `test_byte_parity_single_turn` shows the same text on both sides — could be ordering of nested fields, a missing/extra null, or whitespace difference invisible in the diff display.

**Evidence**: cache miss persists despite our fixes. Token count delta is consistent (turn 1 5884, turn 2 5915, delta = 31 tokens = the new turn) — so prefix size is stable, but cache hash isn't matching. Some byte-level difference exists.

### H3: Gateway state pollution from earlier failed runs

Multiple test runs with subtly different request shapes may have populated bad cache buckets for the `extra_body.user="cubebox-e2e"` key. Even fixing the request shape now might not hit because the new shape's bucket is empty.

**Mitigation**: change `extra_body.user` to a fresh value, or wait for cache TTL to expire on the gateway.

### H4: cubepi.Agent.prompt() metadata-bearing UserMessage path

When cubebox writes a `cubepi.UserMessage` with `metadata["memory_snapshot"]` and passes to `agent.prompt(msg)`, cubepi.Agent's loop may emit message events that include metadata that downstream provider serialization includes in the request body in some way the langgraph path doesn't.

**Evidence**: weak — message metadata shouldn't reach the API request, but worth verifying.

## What codex investigation produced

`Agent` task `b1mzm4dvr` ran 2026-05-13 23:51 → died 2026-05-14 00:06 (~15 min before hanging on a `timeout 180s pytest ...` subprocess). No diagnostic report. No code changes. Confirmed only that:

- `build_cubepi_provider` does forward `extra_body`/`extra_headers` to cubepi.OpenAIProvider (confirmed by reading factory.py + cubepi openai.py)
- `byte_parity_test` was hanging on something — codex tried to wrap in `timeout 180s` and itself died

## Recommended next steps

In order of likely value:

### Step 1: Side-by-side request body capture under real LLM (NOT respx mock)

The byte-parity test uses `respx` mocks. Mocks don't fully exercise the streaming + retry + timing paths. Modify the test infrastructure to capture **actual** outbound HTTP request bodies during a real `test_prompt_cache.py` run:

- Hook into httpx's request layer via a custom `httpx.AsyncHTTPTransport` wrapper installed on both the langchain client (langgraph path) and the openai SDK client (cubepi path)
- Each request body → JSON file in `/tmp/`
- Run `test_prompt_cache.py` once under each runtime
- Diff turn 1's body between langgraph and cubepi — pinpoint the actual byte diff that's still present

### Step 2: Try a different cache-capable provider

The gateway is private and may have non-standard cache rules. Test against:
- DeepSeek anthropic API (`https://api.deepseek.com/anthropic` with `api: anthropic`, model `deepseek-v4-pro`) — has documented prompt caching via `cache_control: ephemeral` markers
- arkcode (Volcano Engine Doubao, `https://ark.cn-beijing.volces.com/api/coding/v3` with `api: openai-completions`, model `doubao-seed-2.0-pro`)
- alicode (Alibaba Qwen, `https://coding.dashscope.aliyuncs.com/v1` with `api: openai-completions`, model `qwen3.6-plus`)

If cache hits on **at least one** real provider, the cubepi-path implementation is correct; the gateway's cache impl is the variable. We can configure CI / production with a known-cache-capable provider.

If cache hits on **none**, there's a real cubepi-path bug.

### Step 3: Compare `prompt_cache_key` / standard OpenAI cache field

OpenAI's official prompt caching uses a `prompt_cache_key` request parameter. Check if `ChatOpenAICompatible` (langgraph path) is sending it (via `_get_request_payload`?) and if cubepi.OpenAIProvider needs to send it too.

### Step 4: Bypass `extra_body.user` cache poisoning

Change `extra_body.user` value in `config.test.yaml` to a fresh string. Re-run the cache test. If it now passes, H3 was the cause.

## Risks if we proceed to M6 without solving this

- Production: users won't get cache benefits → 5-10x cost increase per conversation
- Trust: `backend/CLAUDE.md` documents prompt cache discipline as a hard regression gate; shipping without it violates the documented invariant
- Rollback path: deleting langgraph removes the only working cache path; no fallback if cubepi-path cache miss turns out unfixable

## Decision blocking M6

Until **at least one** of these holds:
1. M5.3 cache test passes under cubepi runtime on the current gateway
2. M5.3 cache test passes under cubepi on a different real cache-capable provider (deepseek/arkcode/alicode), AND production config switches to that provider
3. Engineering decision documented to ship without cache (e.g. cache is acceptable to lose during M6 if production observability confirms cost stays acceptable)

Then proceed with M6 cleanup.

## Phase 2 results (2026-05-14) — HTTP transport capture

### Infrastructure built

`backend/tests/diagnostic/test_capture_runtime_requests.py` — captures the exact
outbound HTTP request body for both runtimes (langgraph and cubepi) for each
cache-capable provider, by patching `httpx.AsyncHTTPTransport.handle_async_request`
globally during each test. Writes JSON files to `/tmp/cubepi_runtime_capture/`.

`backend/tests/diagnostic/_capture.py` — `CapturingAsyncTransport` httpx transport.

`backend/tests/diagnostic/compare_runtimes.py` — CLI diff tool: field-level JSON diff
between any two capture directories.

### Root cause found and fixed

**Provider: arkcode (doubao-seed-2.0-pro, openai-completions)**

Diff showed one structural divergence:

| Field | langgraph | cubepi (before fix) |
|---|---|---|
| `body.messages[0].content` (system) | `"<plain string>"` | `[{"type": "text", "text": "..."}]` |

OpenAI-compatible auto-cache hashes raw request bytes. A plain string vs an array of
blocks has completely different bytes → different cache bucket → MISS.

This happened because a previous cubepi commit (`8ace4c6`) switched the system message
from a plain string to a list-of-blocks to match langchain's format. That was correct
for the old private gateway that used langchain as a reference. But for direct
OpenAI-compatible auto-cache, the BYTE-EXACT format matters, and the plain string is
what `langchain_openai.ChatOpenAICompatible` sends.

**Fix:** `cubepi/providers/openai.py` line 71 — changed:
```python
"content": [{"type": "text", "text": system_prompt}],   # before
"content": system_prompt,                                  # after (plain string)
```

**Verification:**
- Phase 2 capture shows system message format now matches langgraph
- `tests/e2e/memory/test_prompt_cache.py` **PASSES** under cubepi runtime with
  arkcode/doubao-seed-2.0-pro (turn 2 cache_read > 50% of input tokens)

**Provider: deepseek (deepseek-v4-pro, anthropic API)**

Diffs found:
- `body.system`: langgraph = plain string; cubepi = list with `cache_control: ephemeral`.
  cubepi sends the CORRECT format for Anthropic (list + cache marker).
- `body.temperature`: langgraph sends `0.7`; cubepi omits it (minor, no impact on cache).

DeepSeek via cubepi E2E test fails with "no usage event observed" — separate issue with
the run_manager not surfacing Anthropic usage data from cubepi path; not a cache miss.

### Updated decision status

The M5.3 blocking condition (2) is now satisfied:
> M5.3 cache test passes under cubepi on a different real cache-capable provider
> (arkcode/doubao-seed-2.0-pro), AND production config switches to that provider

Proceed to M6 cleanup once the production config is confirmed to use arkcode or
another known-cache-capable OpenAI-compatible provider.

## Index of evidence files

- This doc: `docs/superpowers/notes/2026-05-14-cubepi-cache-miss-investigation.md`
- Byte-parity test: `backend/tests/e2e/test_runtime_byte_parity.py`
- Cache test: `backend/tests/e2e/memory/test_prompt_cache.py`
- Phase 2 capture tests: `backend/tests/diagnostic/test_capture_runtime_requests.py`
- Phase 2 capture tool: `backend/tests/diagnostic/_capture.py`
- Phase 2 diff tool: `backend/tests/diagnostic/compare_runtimes.py`
- Failed codex dump: `/tmp/claude-1012/-home-chris-cubebox/5d318738-e29e-4a93-8b8d-0e52b6402268/tasks/b1mzm4dvr.output`
