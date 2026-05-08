"""Prompt cache hit-rate E2E — regression gate per CLAUDE.md.

Plan task 8.3 + spec §Testing Strategy.

This is the gate that confirms the snapshot-channel design is delivering
real prompt-cache reuse. It runs against a real LLM endpoint and asserts:

    turn 1  : cache_read == 0   (cold)
    turn 2  : cache_read / total >= 0.50
    turn 3+ : cache_read / total >= 0.85

**Currently SKIPPED.** The skip is conditional and lifts as soon as both
prerequisites are in place:

1. The SSE stream emits a `usage` event whose payload is the raw
   provider-reported usage dict. The helper `_send_and_get_usage` below
   reads that event. The agent stream code (cubebox/agents/...) does not
   surface usage today; wiring it is the smallest change needed.

2. The configured LLM provider supports prompt caching (Anthropic
   cache_control or OpenAI auto-cache that hits the bars above). For the
   current OpenAI-compatible endpoint used in dev, auto-cache may exist
   but bars must be verified empirically before enforcing.

DO NOT lower the bars to make this pass — see backend/CLAUDE.md "Prompt
Cache Discipline" / "Cache test failure handling" for the legitimate
debug culprits.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# We skip unconditionally until the SSE `usage` event is wired. Once that
# lands the skip should be conditional on `os.getenv("CUBEBOX_E2E_LLM_*")`
# pointing to a cache-capable provider. Keeping the test in the suite (not
# behind an env-flag opt-in) is intentional — the regression gate is more
# useful when it runs every commit and surfaces config drift early.
pytestmark = pytest.mark.skip(reason="requires SSE usage event emission + cache-capable provider")


def _read_cache_tokens(usage: dict[str, Any]) -> tuple[int, int]:
    """Return (cache_read, total_input). Provider-agnostic.

    Anthropic shape: cache_read_input_tokens, input_tokens, cache_creation_input_tokens
    OpenAI shape:    prompt_tokens_details.cached_tokens, prompt_tokens
    """
    if "cache_read_input_tokens" in usage:
        return (
            int(usage.get("cache_read_input_tokens") or 0),
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0),
        )
    if "prompt_tokens_details" in usage:
        details = usage["prompt_tokens_details"] or {}
        return (
            int(details.get("cached_tokens") or 0),
            int(usage.get("prompt_tokens") or 0),
        )
    raise ValueError(f"unrecognized usage shape: {usage!r}")


async def test_cache_hit_rate_meets_bar() -> None:
    """5-turn fixed script. Bars: turn1=0, turn2>=50%, turn3+>=85%."""
    # Body intentionally unimplemented: the dependencies above land first.
    # When they do, copy the verbatim implementation from plan Task 8.3 and
    # remove the module-level skip marker.
    _ = os.environ
    _ = _read_cache_tokens
    raise NotImplementedError
