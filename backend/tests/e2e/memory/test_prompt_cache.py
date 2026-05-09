"""Real-LLM cache hit-rate E2E (deferred — see #64).

The byte-stability invariants that actually matter for cache reuse are
covered by `tests/unit/test_memory_cache_stability.py` and run on every
commit. That suite catches the design break ("someone added a timestamp
to system prompt", "snapshot reducer was loosened", "pinned sort isn't
deterministic") regardless of LLM provider.

This file is the harder test: send N real turns at a real LLM, read the
provider-reported cache_read tokens, assert hit rate >= bar. It needs:

- An SSE `usage` event surfacing per-call provider usage (not yet wired)
- A provider that reports cache_read tokens — Anthropic with cache_control
  is the design target; OpenAI auto-cache works for OpenAI official
  models but bars need empirical calibration
- The SSE consumer helper from #64 task list

When those land, replace the body below with the verbatim plan-Task-8.3
implementation and remove the skip marker.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="real-LLM cache test deferred to #64; "
    "byte-stability invariants live in tests/unit/test_memory_cache_stability.py"
)


async def test_cache_hit_rate_meets_bar() -> None:
    raise NotImplementedError
