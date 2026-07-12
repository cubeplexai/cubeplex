# cubepi Migration M5 — Testing Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Land the three test tiers that gate M6 cleanup:
1. **Tier 1 unit**: byte-stability of cubepi.Message → API conversion + cubeplex cache marker policy
2. **Tier 2 E2E**: `test_prompt_cache.py` passes under cubepi runtime with the SAME bar as langgraph (turn 2 cache_read ≥50%, final ≥85%)
3. **Tier 3 byte-parity**: fixed conversation scenarios produce byte-identical Anthropic API request bodies through langgraph and cubepi paths

After M5, **M6 default-flag-flip is unblocked**.

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M5.

**Baseline:** M4 done; 742 unit tests pass; 3 cubepi E2Es pass (conversation + tool + history round-trip).

---

## Tasks

### M5.1: Tier 1 unit — byte-stability + cache markers (~8 tests)

`backend/tests/unit/test_cubepi_conversion_stability.py` (new):
- `cubepi.Message → Anthropic API dict` byte-stable across calls (call twice, `canonical_json` equal)
- `cubepi.Message → OpenAI API dict` byte-stable
- Tool definition serialization order deterministic
- `transform_system_prompt` chain output deterministic
- `Message.metadata` msgpack round-trip byte-identical (already covered by cubepi PR's E2E but verify in cubeplex)

Reuse pattern: load deterministic fixtures (e.g. a UserMessage with specific content), call `cubepi.AnthropicProvider._convert_message(msg)`, json.dumps with sort_keys=True, compare against checked-in golden fixtures.

`backend/tests/unit/test_cache_markers_pi.py` (M1.1 already created — extend if needed): ensure `CubeplexCacheMarkerPolicy.message_breakpoint_indices` is byte-stable for the same input (call twice, get identical indices).

### M5.2: Run existing E2E under cubepi + fix anything broken

`config.test.yaml` already has `agents.runtime: cubepi` (M0.4). The full existing E2E suite (auth, conversations CRUD, attachments, citations, todo behaviors, memory, etc.) implicitly runs through cubepi.

Run the **full** real_llm marked E2E suite — record what fails:
```bash
cd backend && uv run pytest tests/e2e -v -m real_llm --tb=short 2>&1 | tee /tmp/e2e-cubepi-run.log
```

For each failure:
- If it's a cubepi-path regression (M3 missed behavior, API contract gap): fix in cubepi `*_pi.py`
- If it's a langgraph-specific test that doesn't apply to cubepi: mark with `@pytest.mark.skip_cubepi` (or whatever existing skip mechanism is)

Land fixes incrementally. Expect 1-3 fixes; this is the "shake out the bugs" milestone.

### M5.3: Tier 2 cache E2E under cubepi

The existing `tests/e2e/memory/test_prompt_cache.py` (gated by `CUBEPLEX_E2E_LLM_CACHE_CAPABLE`) runs at the SSE level so it's runtime-agnostic. Just verify it passes under `agents.runtime=cubepi`:

```bash
CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true uv run pytest tests/e2e/memory/test_prompt_cache.py -v -m real_llm --tb=short
```

If it fails, debug. Likely culprits:
- Memory snapshot byte-instability (M3.b.1 should have prevented this)
- System prompt non-determinism (some middleware injecting time-sensitive content)
- Tool definition ordering shifting between turns

This may be the most informative debugging exercise of M5. Treat it as a long-running diagnostic; iterate on `*_pi.py` files until cache_read ≥50% on turn 2.

### M5.4: Tier 3 byte-parity test (hardest, new infrastructure)

`backend/tests/e2e/test_runtime_byte_parity.py` (new) intercepts outbound HTTP requests via `respx` (already in cubepi dev deps from D4 testing).

**Approach**:

```python
"""Byte-parity test: cubepi and langgraph paths produce identical Anthropic API requests.

For each fixed scenario, run the request through both paths and compare the
outbound HTTP body (sent to the LLM) field-by-field after canonical_json
normalization. Any divergence is a migration regression."""

import json
import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
async def test_byte_parity_simple_user_message(member_client, app) -> None:
    """Fixed scenario: a 2-turn conversation with text-only user messages."""
    
    captured_lg: dict | None = None
    captured_pi: dict | None = None

    # 1. Run scenario via langgraph runtime
    app.state.agents_runtime = "langgraph"
    with respx.mock(assert_all_called=False) as router:
        anthropic_route = router.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=_make_stub_anthropic_response,  # SSE stream stub
        )
        await _run_fixed_scenario(member_client, "byte-parity-lg")
        if anthropic_route.calls:
            captured_lg = json.loads(anthropic_route.calls.last.request.content)

    # 2. Run scenario via cubepi runtime
    app.state.agents_runtime = "cubepi"
    with respx.mock(assert_all_called=False) as router:
        anthropic_route = router.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=_make_stub_anthropic_response,
        )
        await _run_fixed_scenario(member_client, "byte-parity-pi")
        if anthropic_route.calls:
            captured_pi = json.loads(anthropic_route.calls.last.request.content)

    # 3. Compare canonical JSON
    assert captured_lg is not None
    assert captured_pi is not None
    assert _canonical(captured_lg) == _canonical(captured_pi), (
        "Anthropic API request bodies diverge between runtimes:\n"
        f"langgraph: {json.dumps(captured_lg, indent=2, sort_keys=True)}\n"
        f"cubepi:    {json.dumps(captured_pi, indent=2, sort_keys=True)}"
    )


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))
```

**Scenarios to test** (start with 1, add as needed):
- Simple 1-turn text user message → assistant text response
- Tool call: user asks calculator question; capture the request that initiates the tool call
- 2-turn with memory snapshot
- 3-turn with cache markers expected

**Stub Anthropic response**: a deterministic SSE stream that's compatible with both langgraph + cubepi Anthropic clients. Use the existing cubeplex E2E LLM endpoint OR write a hand-crafted SSE response.

**Difficulty**: HTTP interception of langgraph's anthropic call path requires understanding where langchain-anthropic makes requests (it's via the httpx layer most likely, so respx should work). Cubepi.AnthropicProvider uses `anthropic.AsyncAnthropic` which also goes through httpx. Both should be interceptable.

**If respx can't intercept either path cleanly**: use the `on_payload` callback Cubepi providers support; set up a similar capture for langchain via monkeypatching `_agenerate` or similar. Hybrid capture is OK.

**Critical**: this test is the M6 gate. If byte-parity fails for any scenario, M6 doesn't proceed until the divergence is understood + fixed.

### M5.5: Validation + push

- Full unit suite green
- All 3 cubepi-specific E2Es pass
- Tier 1 unit tests added pass
- Tier 2 cache E2E pass under cubepi
- Tier 3 byte-parity test pass for at least 1 scenario (more is better)
- push

---

## Out of scope for M5

- M6 cleanup (separate milestone)
- Performance benchmarks
- Load testing
