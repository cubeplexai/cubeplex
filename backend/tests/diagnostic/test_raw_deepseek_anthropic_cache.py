"""Phase 1a — Raw HTTP cache validation for DeepSeek (Anthropic-compatible API).

This test bypasses cubeplex / cubepi / langchain entirely. It sends two identical
prompts directly to the DeepSeek Anthropic-compatible endpoint using the official
`anthropic` SDK, then verifies that the second request reports
`cache_read_input_tokens > 0`.

Interpretation:
    PASS  → DeepSeek supports prompt cache for this request shape at raw API level.
            Cache misses inside cubepi-runtime are a request-shape / adapter issue
            — fixable at the cubeplex adapter layer.
    FAIL  → DeepSeek does NOT cache even at raw API level → provider limitation,
            unrelated to cubepi migration.
    SKIP  → CUBEPLEX_LLM__PROVIDERS__DEEPSEEK__API_KEY env var not set (safe in CI).
"""

from __future__ import annotations

import pytest

from tests.diagnostic._common import (
    LONG_SYSTEM_PROMPT,
    USER_MSG_TURN_1,
    USER_MSG_TURN_2,
    assert_cache_hit_anthropic,
    extract_anthropic_cache_tokens,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL = "deepseek-v4-pro"

pytestmark = [pytest.mark.real_llm, pytest.mark.diagnostic]


@pytest.mark.asyncio
async def test_deepseek_anthropic_caches_repeated_system_prompt(deepseek_api_key: str) -> None:
    """Send two identical system prompts; assert turn 2 has cache_read_input_tokens > 0.

    The system prompt is marked with cache_control: ephemeral (Anthropic-style).
    The user messages differ so only the system prefix is cached.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=deepseek_api_key, base_url=DEEPSEEK_BASE_URL)

    system_block = [
        {
            "type": "text",
            "text": LONG_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # Turn 1: populate the cache
    resp1 = await client.messages.create(
        model=DEEPSEEK_MODEL,
        max_tokens=32,
        system=system_block,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": USER_MSG_TURN_1}],
    )
    usage1 = extract_anthropic_cache_tokens(resp1.usage)

    # Turn 2: same system, different user message — should read from cache
    resp2 = await client.messages.create(
        model=DEEPSEEK_MODEL,
        max_tokens=32,
        system=system_block,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": USER_MSG_TURN_2}],
    )
    usage2 = extract_anthropic_cache_tokens(resp2.usage)

    assert_cache_hit_anthropic(usage1, usage2, provider_label="DeepSeek/anthropic")
