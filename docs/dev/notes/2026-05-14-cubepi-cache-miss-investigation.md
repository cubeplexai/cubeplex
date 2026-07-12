# cubepi Runtime Path — Prompt Cache Miss Investigation

Date: 2026-05-14
Status: **RESOLVED 2026-05-14** — cache E2E green on DeepSeek anthropic after fixes below.

## Resolution (2026-05-14)

The cache test (`tests/e2e/memory/test_prompt_cache.py` with
`CUBEPLEX_LLM__DEFAULT_MODEL=deepseek/deepseek-v4-pro` and
`CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true`) now passes against DeepSeek's
`/anthropic` surface via the cubepi runtime. Required fixes:

1. **cubepi/providers/anthropic.py** — forward `base_url` to
   `anthropic.AsyncAnthropic`. Without this, DeepSeek's
   `https://api.deepseek.com/anthropic` was silently ignored and the
   client connected to `api.anthropic.com` with a DeepSeek key, which
   failed authentication. Commit `b06aee9` on cubepi
   `feat/cubeplex-readiness`.
2. **cubepi/providers/anthropic.py** — `await stream.get_final_message()`
   (was missing `await`, returned a coroutine object that was passed
   to `_convert_response` and crashed inside the `except BaseException`
   handler). Commit `2d8a0c6`.
3. **cubepi/providers/anthropic.py** — forward `Model.temperature` in
   stream kwargs. Commit `2d8a0c6`.
4. **cubepi/providers/anthropic.py** — conditional system-prompt
   wrapping: send plain string when `cache_policy.mark_system()` is
   False / no cache_control is being applied. Commit `eb9793b`.
5. **cubeplex/llm/factory.py** — pass `provider_config.base_url` when
   constructing `AnthropicProvider`.

Note: cubeplex's `run_manager._dicts_to_sse_events` swallows `error`
typed dicts from `stream_pi.convert_cubepi_agent_event_to_sse`. This
masked the failures and surfaced as "no usage event observed". Not
fixed here; raise as a follow-up (graceful error propagation to the
SSE client is a separate concern).

Original analysis follows below for reference.

## Phase 1 + Phase 2 results

### Phase 1 raw HTTP cache validation (2026-05-14)

| Provider | Raw HTTP cache hit on turn 2? |
|---|---|
| deepseek-v4-pro (anthropic API) | **YES** — 1920/2009 tokens cached |
| arkcode doubao-seed-2.0-pro (openai-completions) | **YES** — 1848/2043 cached |
| alicode qwen3.6-plus (openai-completions) | **NO** — provider returns `cached_tokens=None` (provider-level limitation, not migration issue) |

Conclusion: cache is viable at raw API level on 2 of 3 providers → cubepi-runtime cache miss is a request-shape issue, not a provider capability gap.

### Phase 2 runtime HTTP capture + diff

Captured outbound HTTP request bodies for both langgraph and cubepi runtimes against deepseek and arkcode. Compared via `tests/diagnostic/compare_runtimes.py`.

**DeepSeek anthropic diff (langgraph vs cubepi)** — clear root cause:

| Field | langgraph (cache hits) | cubepi (cache misses) |
|---|---|---|
| `messages[0].content` | plain string `"Reply with exactly: FIRST"` | **list with `cache_control: ephemeral`**: `[{"cache_control": {"type": "ephemeral"}, "text": "Reply with exactly: FIRST", "type": "text"}]` |
| `system` | plain string | **list with `cache_control: ephemeral`** wrapping the text in a block |
| `temperature` | `0.7` | **missing** |

**arkcode openai diff** — only test scaffold artifact, not real divergence:

| Field | langgraph | cubepi |
|---|---|---|
| `stream` | `false` | `true` |
| `stream_options` | (missing) | `{"include_usage": true}` |

This is because the test scaffold used `ainvoke` (non-streaming) for langgraph and `stream()` for cubepi. Not a real cache-relevant diff.

### Diagnosis

cubepi's `AnthropicProvider` puts `cache_control` markers on the USER message (not just system). The Anthropic API spec puts cache markers on system + last completed assistant message; markers on user content are either ignored or invalidate cache. DeepSeek's anthropic-compat endpoint apparently treats them as invalid → cache key changes → miss.

In the capture test scaffold, cubepi was invoked with the default `DefaultCacheMarkerPolicy` (mark last message regardless of role). The cubeplex-runtime path uses `CubeplexCacheMarkerPolicy` (walks back to last AIMessage) which would skip marking on a user-only first turn — needs verification with the real cubeplex runtime path.

Also: cubepi wraps system prompt in a content-block list even when no marker is applied. langgraph (langchain-anthropic) sends system as a plain string in that case. This wrapping changes the cache hash.

Also: cubepi.AnthropicProvider doesn't forward `temperature` from Model config — confirmed missing in the captured request.

### Fix plan (next session)

1. **cubepi anthropic provider** (cubepi side, `/home/chris/cubepi/cubepi/providers/anthropic.py`):
   - Forward `temperature` from `Model` in stream kwargs (similar to the openai fix in commit `97b26a5`)
   - Only wrap content/system in list-of-blocks WHEN `cache_control` is actually being applied to that position; otherwise emit plain string
   - The current logic always wraps the system prompt in a list-of-blocks when system_prompt is non-empty; should only do that when policy.mark_system() is true. Otherwise: `kwargs["system"] = system_prompt` (plain string)

2. **cubeplex CubeplexCacheMarkerPolicy** (already correct):
   - `mark_system()` returns True
   - `message_breakpoint_indices` walks back to last `AssistantMessage`; for a user-only first turn returns []
   - When used in real runtime: this would correctly NOT add cache_control to user message
   - But there's a question: does the real cubeplex runtime even put cache_control on user messages? Need to verify by running e2e cache test + capturing real cubeplex-runtime body (not the simplified phase 2 capture)

3. **Verification**:
   - After fix, re-run capture against DeepSeek; cubepi body should match langgraph's plain-string-system shape (with temperature)
   - Re-run `test_prompt_cache.py` against DeepSeek under cubepi runtime — expect cache_read ≥ 50% on turn 2

### Outstanding: DeepSeek anthropic probe failure with real runtime

When running the real e2e cache test against cubepi + DeepSeek anthropic, the test fails with "Probe failed: no usage event observed at all". Even though cubepi.AnthropicProvider populates AssistantMessage.usage at the end of streaming, the SSE usage event isn't reaching the test. Either:
- cubepi.Agent's MessageEndEvent doesn't carry the final usage data populated by `ms.set_result`
- cubepi.AnthropicProvider's streaming events don't update the partial AssistantMessage's usage along the way (and MessageEndEvent picks up the final usage from elsewhere)

Need to trace why anthropic path's usage isn't flowing through stream_pi → SSE the way openai's does (post-M5.2 fix). Separate from the cache-marker fix above.
Related PR: cubeplex#84 (Draft `feat/integrate-cubepi`), cubepi `feat/cubeplex-readiness`

## Problem

When `config.agents.runtime=cubepi`, `tests/e2e/memory/test_prompt_cache.py::test_cache_hit_rate_meets_bar` reports `cache_read=0` on turn 2. The same test passes when `config.agents.runtime=langgraph` on the **same endpoint + model config**.

This blocks M6: the documented release gate requires the cubepi path to hit ≥ 50% cache_read by turn 2 and ≥ 85% by turn N. Without that gate, deleting the langgraph fallback path is unsafe.

## Test setup

- Endpoint: `https://gateway.chat.sensedeal.vip/v1/chat/completions`
- Model: `qwen3.6-flash`
- Provider type: `openai-completions`
- `extra_body.user: "cubeplex-e2e"` (added in M5.2 to escape a corrupt cached-response bucket; gateway buckets cache by `user` field)
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

### Cubeplex-side fixes

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
  Endpoint declared cache-capable via CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true
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

Multiple test runs with subtly different request shapes may have populated bad cache buckets for the `extra_body.user="cubeplex-e2e"` key. Even fixing the request shape now might not hit because the new shape's bucket is empty.

**Mitigation**: change `extra_body.user` to a fresh value, or wait for cache TTL to expire on the gateway.

### H4: cubepi.Agent.prompt() metadata-bearing UserMessage path

When cubeplex writes a `cubepi.UserMessage` with `metadata["memory_snapshot"]` and passes to `agent.prompt(msg)`, cubepi.Agent's loop may emit message events that include metadata that downstream provider serialization includes in the request body in some way the langgraph path doesn't.

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
- Failed codex dump: `/tmp/claude-1012/-home-chris-cubeplex/5d318738-e29e-4a93-8b8d-0e52b6402268/tasks/b1mzm4dvr.output`
