"""Phase 1b — Raw HTTP cache validation for alicode (DashScope coding, OpenAI-spec).

This test bypasses cubebox / cubepi / langchain entirely. It sends two identical
prompts directly to `https://coding.dashscope.aliyuncs.com/v1` using the official
`openai` SDK, then verifies that the second request reports cached_tokens > 0 in
the usage object.

OpenAI-spec providers use auto-cache: no explicit `cache_control` markers needed.
The cache activates when the byte-prefix is identical across calls (≥ 1024 tokens).

Interpretation:
    PASS  → alicode supports prompt cache for this request shape at raw API level.
            Cache misses inside cubepi-runtime are a request-shape / adapter issue.
    FAIL  → alicode does NOT cache even at raw API level → provider limitation.
    SKIP  → alicode credentials not available locally (safe in CI).
"""

from __future__ import annotations

import pytest

from tests.diagnostic._common import (
    LONG_SYSTEM_PROMPT,
    USER_MSG_TURN_1,
    USER_MSG_TURN_2,
    assert_cache_hit_openai,
    extract_openai_cache_tokens,
)

ALICODE_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
ALICODE_MODEL = "qwen3.6-plus"

pytestmark = [pytest.mark.real_llm, pytest.mark.diagnostic]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="alicode (DashScope qwen3.6-plus) does not expose prompt cache "
    "in the usage object via this endpoint. Confirmed empirically Phase 1: "
    "cached_tokens=None on both turns. Kept as scaffold to re-verify if "
    "DashScope adds cache support; do not block CI on it.",
)
async def test_alicode_openai_caches_repeated_system_prompt(alicode_api_key: str) -> None:
    """Send two identical system prompts; assert turn 2 has cached_tokens > 0.

    No explicit cache_control markers — OpenAI auto-cache activates when the
    byte-prefix is identical across requests.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=alicode_api_key, base_url=ALICODE_BASE_URL)

    # Turn 1: populate the cache
    resp1 = await client.chat.completions.create(
        model=ALICODE_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_1},
        ],
    )
    usage1 = extract_openai_cache_tokens(resp1.usage, provider="alicode")
    print(f"\n[alicode] Full turn 1 usage object: {resp1.usage!r}")

    # Turn 2: same system prefix, different user message — should hit cache
    resp2 = await client.chat.completions.create(
        model=ALICODE_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_2},
        ],
    )
    usage2 = extract_openai_cache_tokens(resp2.usage, provider="alicode")
    print(f"[alicode] Full turn 2 usage object: {resp2.usage!r}")

    assert_cache_hit_openai(usage1, usage2, provider_label="alicode/qwen3.6-plus")
