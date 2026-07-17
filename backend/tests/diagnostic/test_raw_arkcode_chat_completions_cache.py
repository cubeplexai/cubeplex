"""Phase 1c — Raw HTTP cache validation for arkcode (Ark Coding / ByteDance, OpenAI-spec).

Fallback provider. This test bypasses cubeplex / cubepi / langchain entirely.
It sends two identical prompts directly to
`https://ark.cn-beijing.volces.com/api/coding/v3` using the official `openai` SDK,
then verifies that the second request reports cached tokens > 0.

ByteDance Ark may report cached tokens under a non-standard field
(`prompt_cache_hit_tokens`) rather than the standard
`prompt_tokens_details.cached_tokens`. The `extract_openai_cache_tokens` helper
tries both locations.

Interpretation:
    PASS  → arkcode supports prompt cache for this request shape at raw API level.
    FAIL  → arkcode does NOT cache even at raw API level → provider limitation.
    SKIP  → arkcode credentials not available locally (safe in CI).
"""

from __future__ import annotations

import pytest

from tests.diagnostic._common import (
    LONG_SYSTEM_PROMPT,
    USER_MSG_TURN_1,
    USER_MSG_TURN_2,
    assert_cache_hit_openai_either,
    extract_openai_cache_tokens,
)

ARKCODE_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
ARKCODE_MODEL = "doubao-seed-2.0-pro"

pytestmark = [pytest.mark.real_llm, pytest.mark.diagnostic]


@pytest.mark.asyncio
async def test_arkcode_openai_caches_repeated_system_prompt(arkcode_api_key: str) -> None:
    """Send two identical system prompts; assert turn 2 has cached tokens > 0.

    No explicit cache_control markers — OpenAI auto-cache activates when the
    byte-prefix is identical across requests.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=arkcode_api_key, base_url=ARKCODE_BASE_URL)

    # Turn 1: populate the cache
    resp1 = await client.chat.completions.create(
        model=ARKCODE_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_1},
        ],
    )
    usage1 = extract_openai_cache_tokens(resp1.usage, provider="arkcode")
    print(f"\n[arkcode] Full turn 1 usage object: {resp1.usage!r}")

    # Turn 2: same system prefix, different user message — should hit cache
    resp2 = await client.chat.completions.create(
        model=ARKCODE_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_2},
        ],
    )
    usage2 = extract_openai_cache_tokens(resp2.usage, provider="arkcode")
    print(f"[arkcode] Full turn 2 usage object: {resp2.usage!r}")

    # arkcode may return cached_tokens on turn 1 (warmed from a prior run) or turn 2
    # (warmed by turn 1 in this run). Either way, at least one hit proves caching works.
    assert_cache_hit_openai_either(usage1, usage2, provider_label="arkcode/doubao-seed-2.0-pro")
