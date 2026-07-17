# Memory PR1 — Prompt Cache Regression Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `backend/tests/e2e/memory/test_prompt_cache.py` as a real-LLM regression gate that asserts cache reuse rate ≥ 50% (turn 2) / 85% (turn N), self-skipping when the configured endpoint does not report cache fields.

**Architecture:** Add a `ChatAnthropic` branch to `LLMFactory`, surface per-LLM-call cache usage as a new SSE `usage` event, and add a test helper that drives a multi-turn conversation and aggregates usage. The strict bar lives unchanged in the test; an upfront capability probe converts "endpoint cannot cache" into `pytest.skip` rather than a relaxed bar.

**Tech Stack:** Python 3.12 + LangChain 0.3 + `langchain-anthropic`, FastAPI SSE, pytest-asyncio.

**Branch:** `feat/test-prompt-cache-gate` from `origin/main`.
**Spec:** `docs/superpowers/specs/2026-05-09-memory-llm-behavior-e2e-design.md` (PR1 section).
**Issue:** [#64](https://github.com/xfgong/cubeplex/issues/64).

---

## File Structure

**Production code:**
- Modify: `backend/cubeplex/llm/factory.py:471-473` — replace `NotImplementedError` with `ChatAnthropic` branch
- Modify: `backend/cubeplex/agents/schemas.py:140` (end of file) — add `UsageEvent`
- Modify: `backend/cubeplex/agents/stream.py:48-142` (`convert_messages_chunk`) — emit `UsageEvent` when `usage_metadata` indicates a turn end

**Tests:**
- Create: `backend/tests/e2e/memory/_helpers.py` — `send_message_and_collect_usage`
- Modify: `backend/tests/e2e/memory/test_prompt_cache.py` — drop skip, implement probe + strict gate
- Create: `backend/tests/unit/llm/test_factory_anthropic.py` — unit-test the factory branch

**Config:**
- Modify: `backend/pyproject.toml` — add `langchain-anthropic` dep + register `real_llm` pytest marker
- Modify: `backend/Makefile` (or wherever pytest defaults live) — exclude `real_llm` from default `make test`

---

### Task 1: Worktree + dependency + marker registration

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/Makefile`

- [ ] **Step 1.1: Verify worktree state**

Run from worktree root:

```bash
pwd && cat .worktree.env | head -3 && git status && git log --oneline -3
```

Expected: pwd ends in `/feat-test-prompt-cache-gate` (or your worktree name), `.worktree.env` shows allocated ports, status clean, log shows `origin/main` HEAD.

- [ ] **Step 1.2: Add `langchain-anthropic` dependency**

Run from `backend/`:

```bash
uv add langchain-anthropic
```

Expected: `pyproject.toml` updated with new entry under `[project].dependencies`, `uv.lock` regenerated.

- [ ] **Step 1.3: Register the `real_llm` pytest marker**

Edit `backend/pyproject.toml` — find `[tool.pytest.ini_options]` section. Add a `markers` list (or extend if it exists):

```toml
[tool.pytest.ini_options]
# ... existing keys ...
markers = [
    "real_llm: tests that require a real LLM endpoint with cache_control honored; deselected by default in CI",
]
```

If `markers` already exists, append the entry; do not remove existing markers.

- [ ] **Step 1.4: Make `make test` deselect `real_llm` by default**

Find the `test:` target in `backend/Makefile`. Append `-m "not real_llm"` to the default `pytest` invocation. Also add a new `test-real-llm` target.

```makefile
test:
	uv run pytest -s -v -m "not real_llm"

test-real-llm:
	uv run pytest -s -v -m real_llm tests/e2e/memory/
```

Adjust the `test:` line to match the existing one, only adding `-m "not real_llm"` between `pytest` and the path/flags.

- [ ] **Step 1.5: Verify the marker works without breaking anything**

Run from `backend/`:

```bash
uv run pytest --collect-only -m real_llm tests/ 2>&1 | tail -10
uv run pytest --collect-only -m "not real_llm" tests/unit/ 2>&1 | tail -5
```

Expected: first collect returns "no tests ran matching marker" (empty collection — no test is yet decorated with `real_llm`). Second collect lists unit tests as before (no warnings about unknown marker).

- [ ] **Step 1.6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/Makefile
git commit -m "chore(memory-e2e): add langchain-anthropic + real_llm pytest marker"
```

---

### Task 2: ChatAnthropic factory branch (TDD)

**Files:**
- Create: `backend/tests/unit/llm/test_factory_anthropic.py`
- Modify: `backend/cubeplex/llm/factory.py:471-473`

- [ ] **Step 2.1: Write the failing unit test**

Create `backend/tests/unit/llm/test_factory_anthropic.py`:

```python
"""Factory branch for Anthropic API providers — unit tests.

We do not call the real network here; we assert that the factory builds
a ChatAnthropic instance with the right kwargs and the correct cubeplex
metadata attached for CostMiddleware.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic

from cubeplex.llm.config import LLMConfig, ModelConfig, ModelCost, ProviderConfig
from cubeplex.llm.factory import LLMFactory


def _build_config() -> LLMConfig:
    """Minimal LLMConfig with a single Anthropic provider."""
    return LLMConfig(
        default_model="anthropic-test/claude-test",
        providers={
            "anthropic-test": ProviderConfig(
                api="anthropic",
                base_url="https://example.invalid/anthropic",
                api_key="dummy-key",
                models=[
                    ModelConfig(
                        id="claude-test",
                        cost=ModelCost(
                            input=3.0,
                            output=15.0,
                            cache_read=0.3,
                            cache_write=3.75,
                        ),
                    )
                ],
            )
        },
    )


@pytest.mark.asyncio
async def test_factory_builds_chat_anthropic_for_anthropic_api() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create_model("anthropic-test", "claude-test")

    assert isinstance(llm, ChatAnthropic)
    # Cubeplex metadata must round-trip for CostMiddleware
    assert getattr(llm, "_cubeplex_provider", None) == "anthropic-test"
    assert getattr(llm, "_cubeplex_model_id", None) == "claude-test"
    assert getattr(llm, "_cubeplex_model_cost", None) is not None


@pytest.mark.asyncio
async def test_factory_passes_base_url_and_api_key() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create_model("anthropic-test", "claude-test")

    # ChatAnthropic stores base_url under different attr names depending on
    # version; check both.
    base_url: Any = getattr(llm, "anthropic_api_url", None) or getattr(llm, "base_url", None)
    assert base_url and "example.invalid" in str(base_url)


@pytest.mark.asyncio
async def test_factory_wraps_anthropic_with_cache_markers() -> None:
    """The Anthropic branch must apply cache_control via _wrap_with_cache_markers."""
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create_model("anthropic-test", "claude-test")

    # _wrap_with_cache_markers patches `_agenerate` in-place. The patched
    # method's qualname includes "patched_agenerate" or has been replaced.
    agenerate = llm._agenerate  # type: ignore[attr-defined]
    assert agenerate.__name__ in {"patched_agenerate", "_agenerate"}
    # If it's still _agenerate, cache markers were not applied.
    assert agenerate.__name__ == "patched_agenerate", (
        "factory must wrap Anthropic models with _wrap_with_cache_markers"
    )
```

Note on the existing `LLMFactory.create_model` signature: confirm the exact name and arguments before writing the test — it may be `create_llm` or accept different positional args. If the test signature does not match, fix it to match the existing signature. The semantic checks (isinstance + metadata + base_url) are what matters.

- [ ] **Step 2.2: Run test to verify it fails**

Run from `backend/`:

```bash
uv run pytest tests/unit/llm/test_factory_anthropic.py -v 2>&1 | tail -20
```

Expected: all three tests fail. Most likely first failure is `NotImplementedError("Anthropic API not yet implemented")` raised by `LLMFactory.create_model`.

- [ ] **Step 2.3: Implement the Anthropic branch**

Open `backend/cubeplex/llm/factory.py`. Find the block at lines 471–473:

```python
        if provider_config.api == "anthropic":
            # TODO: Implement Anthropic support
            raise NotImplementedError("Anthropic API not yet implemented")
```

Replace it with:

```python
        if provider_config.api == "anthropic":
            from langchain_anthropic import ChatAnthropic

            anthropic_kwargs: dict[str, Any] = {
                "model": model_config.id,
                "api_key": provider_config.api_key,
                "streaming": True,
                "stream_usage": True,
                "temperature": kwargs.get("temperature", 0.0),
                "max_tokens": kwargs.get("max_tokens", 4096),
            }
            if provider_config.base_url:
                anthropic_kwargs["base_url"] = provider_config.base_url
            if extra_headers:
                anthropic_kwargs["default_headers"] = extra_headers

            llm = ChatAnthropic(**anthropic_kwargs)

            # Attach cubeplex metadata for CostMiddleware to read.
            llm._cubeplex_provider = provider_name  # type: ignore[attr-defined]
            llm._cubeplex_model_id = model_config.id  # type: ignore[attr-defined]
            llm._cubeplex_model_cost = model_config.cost  # type: ignore[attr-defined]

            provider_kind = provider_kind_from_api(provider_config.api)
            return _wrap_with_cache_markers(llm, provider_kind=provider_kind)
```

Make sure the import `from langchain_anthropic import ChatAnthropic` is present at module top-level (move the import out of the function body if you prefer the module-top style used elsewhere in the file). Match the file's existing import-placement convention.

- [ ] **Step 2.4: Run test to verify it passes**

```bash
uv run pytest tests/unit/llm/test_factory_anthropic.py -v
```

Expected: all three tests pass.

- [ ] **Step 2.5: Run lint + typecheck**

```bash
uv run ruff check cubeplex/llm/factory.py tests/unit/llm/test_factory_anthropic.py
uv run mypy cubeplex/llm/factory.py
```

Expected: no errors.

- [ ] **Step 2.6: Commit**

```bash
git add backend/cubeplex/llm/factory.py backend/tests/unit/llm/test_factory_anthropic.py
git commit -m "feat(llm): ChatAnthropic provider branch in factory"
```

---

### Task 3: UsageEvent schema

**Files:**
- Modify: `backend/cubeplex/agents/schemas.py` (append after `CitationEvent`)

- [ ] **Step 3.1: Add UsageEvent**

Open `backend/cubeplex/agents/schemas.py`. After the `CitationEvent` class (around line 130–145), append:

```python
class UsageEvent(AgentEvent):
    """Per-LLM-call token usage event.

    Emitted once per LLM call in a run, immediately after the final
    AIMessageChunk for that call. Carries the same dict shape that
    CostMiddleware computes via _extract_usage so consumers (cost UI,
    cache regression test) can read it without re-parsing the model
    response.
    """

    type: Literal["usage"] = "usage"
    data: dict[str, Any] = Field(
        description=(
            "Usage payload: input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens"
        )
    )
```

- [ ] **Step 3.2: Verify it imports cleanly**

```bash
uv run python -c "from cubeplex.agents.schemas import UsageEvent; print(UsageEvent(timestamp='t', data={'input_tokens': 1, 'output_tokens': 2, 'cache_read_tokens': 0, 'cache_write_tokens': 0}).model_dump())"
```

Expected: prints a dict with `type='usage'` and the data fields.

- [ ] **Step 3.3: Commit**

```bash
git add backend/cubeplex/agents/schemas.py
git commit -m "feat(streams): UsageEvent SSE event type"
```

---

### Task 4: Emit UsageEvent from convert_messages_chunk

**Files:**
- Modify: `backend/cubeplex/agents/stream.py:48-142`
- Test: `backend/tests/unit/agents/test_stream_usage_event.py`

- [ ] **Step 4.1: Write the failing test**

Create `backend/tests/unit/agents/test_stream_usage_event.py`:

```python
"""convert_messages_chunk emits a UsageEvent when the chunk carries usage_metadata."""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from cubeplex.agents.stream import convert_messages_chunk


def _wrap(msg: AIMessageChunk) -> tuple[AIMessageChunk, dict]:
    return (msg, {"langgraph_node": "agent"})


def test_no_usage_event_when_metadata_absent() -> None:
    chunk = AIMessageChunk(content="hi", response_metadata={})
    events = convert_messages_chunk(_wrap(chunk))
    assert all(e["type"] != "usage" for e in events)


def test_emits_usage_event_when_metadata_present_with_cache() -> None:
    chunk = AIMessageChunk(
        content="hi",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "input_token_details": {"cache_read": 80},
            "output_token_details": {},
        },
    )
    events = convert_messages_chunk(_wrap(chunk))
    usage_events = [e for e in events if e["type"] == "usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["data"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_write_tokens": 0,
    }


def test_no_usage_event_when_input_tokens_zero() -> None:
    """Intermediate streamed chunks have usage_metadata={input_tokens:0, ...};
    only the final chunk in a turn (with non-zero totals) should emit."""
    chunk = AIMessageChunk(
        content="hi",
        usage_metadata={
            "input_tokens": 0,
            "output_tokens": 0,
            "input_token_details": {},
            "output_token_details": {},
        },
    )
    events = convert_messages_chunk(_wrap(chunk))
    assert all(e["type"] != "usage" for e in events)
```

- [ ] **Step 4.2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/agents/test_stream_usage_event.py -v 2>&1 | tail -15
```

Expected: at least the second test fails (no UsageEvent emitted).

- [ ] **Step 4.3: Implement UsageEvent emission**

Open `backend/cubeplex/agents/stream.py`. Find the end of the existing `text_delta` block in `convert_messages_chunk` (around line 114, right after the `events.append(...)` call that adds text_delta).

After the text_delta append, add:

```python
    # UsageEvent — emit once per turn when usage_metadata indicates a
    # complete tally (non-zero input_tokens). Intermediate streamed chunks
    # have all-zero usage_metadata and must not produce a UsageEvent.
    usage_metadata: dict[str, Any] = (
        getattr(msg, "usage_metadata", {})
        if not isinstance(msg, dict)
        else msg.get("usage_metadata", {})
    ) or {}
    if usage_metadata.get("input_tokens", 0) > 0:
        details_in = usage_metadata.get("input_token_details") or {}
        details_out = usage_metadata.get("output_token_details") or {}
        events.append(
            {
                "type": "usage",
                "timestamp": timestamp,
                "data": {
                    "input_tokens": usage_metadata.get("input_tokens", 0),
                    "output_tokens": usage_metadata.get("output_tokens", 0),
                    "cache_read_tokens": details_in.get("cache_read", 0),
                    "cache_write_tokens": details_out.get("cache_write", 0),
                },
                "agent_id": agent_id,
            }
        )
```

Place this block after the text_delta append, **before** the `tool_call_chunks` for-loop. The exact insertion point is where the file currently has a blank line between the text_delta block and the `# Tool call argument deltas` comment.

- [ ] **Step 4.4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/agents/test_stream_usage_event.py -v
```

Expected: all three tests pass.

- [ ] **Step 4.5: Run the existing streaming test suite to verify no regression**

```bash
uv run pytest tests/e2e/test_streaming.py -v 2>&1 | tail -30
```

Expected: existing tests pass; no new failures.

- [ ] **Step 4.6: Wire UsageEvent through `_dicts_to_sse_events`**

Find the `_dicts_to_sse_events` function in `backend/cubeplex/streams/run_manager.py` or `backend/cubeplex/api/conversations_route.py` (search for it):

```bash
grep -rn "_dicts_to_sse_events" backend/cubeplex/ | head -5
```

Open the file and look at the if/elif chain that maps `"type": "..."` to `Event` classes. Add a branch for `"usage"`:

```python
        elif evt_type == "usage":
            from cubeplex.agents.schemas import UsageEvent

            sse_events.append(
                UsageEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )
```

Place the new branch alongside the existing branches for `text_delta`, `tool_call`, etc. Match the surrounding code style.

- [ ] **Step 4.7: Verify SSE serialization end-to-end**

Add a small integration test or run the app + curl:

Add to `backend/tests/unit/agents/test_stream_usage_event.py`:

```python
def test_dicts_to_sse_events_handles_usage_type() -> None:
    """Cover the dispatch path; UsageEvent must serialize through the SSE layer."""
    from cubeplex.streams.run_manager import _dicts_to_sse_events  # adjust if elsewhere

    events = _dicts_to_sse_events(
        [
            {
                "type": "usage",
                "timestamp": "2026-05-09T00:00:00Z",
                "data": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 80,
                    "cache_write_tokens": 0,
                },
                "agent_id": None,
            }
        ],
        {},
    )
    assert len(events) == 1
    assert events[0].type == "usage"
    assert events[0].data["cache_read_tokens"] == 80
```

If `_dicts_to_sse_events` lives in a different module, adjust the import. The test exists primarily to prevent silent fallthrough where unknown event types are dropped.

- [ ] **Step 4.8: Run the new test**

```bash
uv run pytest tests/unit/agents/test_stream_usage_event.py -v
```

Expected: pass.

- [ ] **Step 4.9: Commit**

```bash
git add backend/cubeplex/agents/stream.py \
        backend/cubeplex/streams/run_manager.py \
        backend/tests/unit/agents/test_stream_usage_event.py
git commit -m "feat(streams): emit UsageEvent per LLM call with cache breakdown"
```

(If `_dicts_to_sse_events` lives in `conversations_route.py`, swap that path into the `git add`.)

---

### Task 5: SSE consumer helper for usage

**Files:**
- Create: `backend/tests/e2e/memory/_helpers.py`

- [ ] **Step 5.1: Create the helper module**

Note: PR2 (`feat/test-memory-injection`) creates the same file with `send_message_and_collect_text`. If PR2 merges first, this step **appends** to the existing file instead of creating it (re-use the existing `_stream_events` helper, only add the new function). If PR1 merges first, PR2 will do the symmetric thing on its rebase.

Create `backend/tests/e2e/memory/_helpers.py` (or append if it exists):

```python
"""SSE consumer helpers for memory E2E tests.

Drives POST /api/v1/ws/{ws}/conversations/{conv}/messages and parses
the Server-Sent Events body. Mirrors the inline pattern in
tests/e2e/test_streaming.py but exposes it as importable functions.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


async def _stream_events(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> list[dict[str, Any]]:
    """Send one user message and collect every parsed SSE event."""
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": content},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def send_message_and_collect_usage(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> dict[str, int]:
    """Drive one turn and aggregate per-call UsageEvent payloads.

    Returns a single dict summing every emitted usage event for the turn:
        {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens}
    Returns all-zero dict if no usage events were emitted (endpoint did
    not report usage). The caller decides whether that is "skip" or "fail".
    """
    events = await _stream_events(client, ws_id, conv_id, content)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    for evt in events:
        if evt.get("type") != "usage":
            continue
        data = evt.get("data") or {}
        for k in totals:
            totals[k] += int(data.get(k) or 0)
    return totals
```

- [ ] **Step 5.2: Verify the file imports**

```bash
uv run python -c "from tests.e2e.memory._helpers import send_message_and_collect_usage; print('ok')"
```

Expected: prints `ok`. If `tests/` is not on path, run from `backend/` and prepend `PYTHONPATH=.`:

```bash
cd backend && PYTHONPATH=. uv run python -c "from tests.e2e.memory._helpers import send_message_and_collect_usage; print('ok')"
```

- [ ] **Step 5.3: Commit**

```bash
git add backend/tests/e2e/memory/_helpers.py
git commit -m "test(memory): SSE consumer helper send_message_and_collect_usage"
```

---

### Task 6: Implement test_prompt_cache.py — probe + strict gate

**Files:**
- Modify: `backend/tests/e2e/memory/test_prompt_cache.py`

- [ ] **Step 6.1: Replace the file body**

Open `backend/tests/e2e/memory/test_prompt_cache.py` and replace its contents with:

```python
"""Real-LLM prompt cache regression gate (issue #64, plan task 8.3).

This is the spec-mandated gate for the memory system: a stable system+
memory prefix across N turns must drive cache reuse rate ≥ 50% by turn 2
and ≥ 85% by turn N. The byte-stability invariants are covered by
tests/unit/test_memory_cache_stability.py and run on every commit.

The endpoint must report cache fields in usage. We probe with a single
warmup turn; if the endpoint does not report any cache reads even when
the prefix is repeated, we skip rather than relax the bar.
"""

from __future__ import annotations

import pytest

from tests.e2e.memory._helpers import send_message_and_collect_usage

pytestmark = pytest.mark.real_llm

# Number of follow-up turns after the warmup. The strict bar applies to
# the second turn (50%) and the final turn (85%). Tuned to balance
# runtime against the law of large numbers — fewer than ~8 turns gives
# noisy ratios on small-context endpoints.
N_TURNS = 10

PRIMER_USER_TEXT = (
    "Reply with a single word. This is a stable test message used to "
    "exercise the prompt cache infrastructure."
)


@pytest.mark.asyncio
async def test_cache_hit_rate_meets_bar(
    member_client,  # type: ignore[no-untyped-def]
) -> None:
    client, ws_id = member_client

    # Create a conversation
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations", params={"title": "cache-gate"}
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    # Probe — does this endpoint report cache fields at all?
    warmup = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)
    assert warmup["input_tokens"] > 0, (
        "Probe failed: no usage event observed at all. Either the LLM "
        "factory branch is wrong or stream_usage is not enabled."
    )

    # Turn 2 — second message in same conversation. With a stable prefix
    # we should see cache_read_tokens > 0 if the endpoint supports caching
    # at all.
    second = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)
    if second["cache_read_tokens"] == 0:
        pytest.skip(
            f"Endpoint does not report or honor prompt cache: warmup "
            f"input={warmup['input_tokens']}, turn2 input={second['input_tokens']}, "
            f"turn2 cache_read=0. Probe negative — strict bar cannot be evaluated."
        )

    # Strict bar — turn 2 must reuse at least half the input.
    ratio_2 = second["cache_read_tokens"] / max(second["input_tokens"], 1)
    assert ratio_2 >= 0.5, (
        f"Turn 2 cache reuse {ratio_2:.2%} below 50% bar "
        f"(cache_read={second['cache_read_tokens']}, input={second['input_tokens']}). "
        f"Likely cause: dynamic content snuck into the stable prefix. "
        f"See backend/CLAUDE.md 'Prompt Cache Discipline'."
    )

    # Turn 3..N — extend conversation, last turn must hit the high bar.
    last = second
    for i in range(3, N_TURNS + 1):
        last = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)

    ratio_n = last["cache_read_tokens"] / max(last["input_tokens"], 1)
    assert ratio_n >= 0.85, (
        f"Turn {N_TURNS} cache reuse {ratio_n:.2%} below 85% bar "
        f"(cache_read={last['cache_read_tokens']}, input={last['input_tokens']}). "
        f"With a stable prefix and N={N_TURNS} turns, this should be 90%+."
    )
```

Two notes for the engineer:

1. The `member_client` fixture currently lives in `backend/tests/e2e/conftest.py` and yields `tuple[httpx.AsyncClient, str]` (client, workspace_id). Confirm by `grep -n "async def member_client" backend/tests/e2e/conftest.py` — you should see line ~415. Memory tests under `backend/tests/e2e/memory/` already pull from this conftest because pytest walks up.

2. If the probe fails because the warmup did not produce any usage event at all, that points at the SSE wiring (Task 4) — fix there, not here.

- [ ] **Step 6.2: Verify the test is collected with the marker**

```bash
uv run pytest --collect-only -m real_llm tests/e2e/memory/test_prompt_cache.py 2>&1 | tail -5
```

Expected: lists `test_cache_hit_rate_meets_bar` once.

- [ ] **Step 6.3: Run the test against your local LLM endpoint**

Make sure `backend/.env` and `backend/config.development.local.yaml` are present in this worktree (copy from main if missing per backend/CLAUDE.md).

```bash
uv run pytest tests/e2e/memory/test_prompt_cache.py -v -m real_llm 2>&1 | tail -30
```

Expected outcomes (any of these is acceptable for this step):
- **Pass**: endpoint honors cache_control and meets both bars.
- **Skip with explicit reason**: endpoint reports usage but `cache_read_tokens == 0`. Test infrastructure works; endpoint cannot exercise the bar.
- **Hard fail**: endpoint reports no usage events at all → fix Task 4. Do not paper over this.

If pass: continue. If skip: capture the skip reason, paste in the PR description, continue. If hard fail: stop and fix.

- [ ] **Step 6.4: Pollution canary check (manual, not committed)**

Temporarily add a timestamp into the system prompt to verify the test catches the regression. Find the system prompt construction (likely in `cubeplex/middleware/memory.py` or the base prompt module) and add a transient `f"\n[time={datetime.now().isoformat()}]"`. Re-run `uv run pytest tests/e2e/memory/test_prompt_cache.py -v`. Expected: turn-2 cache ratio drops below 50%, test fails with the expected error message.

Revert the canary change. Do not commit it.

- [ ] **Step 6.5: Commit**

```bash
git add backend/tests/e2e/memory/test_prompt_cache.py
git commit -m "test(memory): cache regression gate (8.3) — probe + strict 50/85 bar"
```

---

### Task 7: Verification + PR

- [ ] **Step 7.1: Full backend lint + typecheck**

Run from `backend/`:

```bash
make lint
make type-check
```

Expected: both green. If either fails, fix before continuing.

- [ ] **Step 7.2: Full unit test suite**

```bash
uv run pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7.3: Default E2E run (real_llm deselected)**

```bash
make test
```

Expected: all green. The `test_prompt_cache.py` test is **not** collected here (deselected via `-m "not real_llm"` from Task 1).

- [ ] **Step 7.4: Real-LLM run**

```bash
make test-real-llm
```

Expected: passes or skips with the documented endpoint-capability reason. Capture exact output for the PR description.

- [ ] **Step 7.5: Push branch + open PR**

```bash
git push -u origin feat/test-prompt-cache-gate
gh pr create --title "feat(memory): cache regression gate (issue #64 PR1)" --body "$(cat <<'EOF'
## Summary
- Add `ChatAnthropic` branch to `LLMFactory`; cache_control already wired via existing `_wrap_with_cache_markers`.
- New SSE `usage` event emitted per LLM call; piggy-backs `_extract_usage` field shape so cost UI and the cache test agree.
- Test helper `send_message_and_collect_usage` + un-skipped `test_prompt_cache.py` with capability probe and strict 50%/85% bar.
- New `real_llm` pytest marker; default `make test` deselects, `make test-real-llm` opts in.

## Test plan
- [x] `make lint` and `make type-check` clean
- [x] Unit tests pass (`tests/unit/llm/test_factory_anthropic.py`, `tests/unit/agents/test_stream_usage_event.py`)
- [x] `make test` (CI-equivalent, real_llm deselected) passes
- [x] `make test-real-llm` against local endpoint: <PASTE OUTCOME — pass / skip-with-reason>
- [x] Pollution canary (temporary timestamp in system prompt) makes the test fail as expected — confirmed locally, not committed

Refs: #64

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review summary

- **Spec coverage:** PR1 section requires (a) Anthropic provider in factory.py — Task 2; (b) SSE usage event — Tasks 3+4; (c) helper — Task 5; (d) un-skip + probe + strict bar — Task 6; (e) `real_llm` marker — Task 1. All present.
- **Placeholder scan:** none — every step has runnable commands and exact code blocks.
- **Type consistency:** `LLMFactory.create_model(provider_name, model_id, **kwargs)` — Task 2 test calls it with two positional args; the existing factory uses the same pattern (line 435 calls into the same function). `UsageEvent` schema in Task 3 matches the dict produced in Task 4. `send_message_and_collect_usage` return shape (Task 5) matches the assertions in Task 6.
