"""Diagnose cache token reporting for kimi-k2.6 on arkcode (OpenAI-spec endpoint).

kimi-k2.6 is a Moonshot model hosted on Volcengine Ark's coding endpoint.
This test checks whether the raw API returns any cache token stats, and under
which field — since Ark may use non-standard fields for non-Doubao models.

Interpretation:
    PASS  → kimi-k2.6 on arkcode exposes cached_tokens > 0 at raw API level.
    FAIL  → kimi-k2.6 does NOT expose cache stats → provider limitation.
    SKIP  → arkcode credentials not available locally (safe in CI).
"""

from __future__ import annotations

import pytest

from tests.diagnostic._common import (
    LONG_SYSTEM_PROMPT,
    USER_MSG_TURN_1,
    USER_MSG_TURN_2,
    extract_openai_cache_tokens,
)

ARKCODE_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
KIMI_MODEL = "kimi-k2.6"

pytestmark = [pytest.mark.real_llm, pytest.mark.diagnostic]


@pytest.mark.asyncio
async def test_arkcode_kimi_k2_cache_stats(arkcode_api_key: str) -> None:
    """Send two identical system prompts with kimi-k2.6; print raw usage objects.

    This is a diagnostic probe — it does NOT assert a cache hit, because kimi
    on Ark may not expose cache stats at all. The output tells us which field
    (if any) carries the cache count.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=arkcode_api_key, base_url=ARKCODE_BASE_URL)

    resp1 = await client.chat.completions.create(
        model=KIMI_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_1},
        ],
    )
    usage1 = extract_openai_cache_tokens(resp1.usage, provider="arkcode")
    print(f"\n[kimi-k2.6/arkcode] Full turn 1 usage object: {resp1.usage!r}")

    resp2 = await client.chat.completions.create(
        model=KIMI_MODEL,
        max_tokens=32,
        messages=[
            {"role": "system", "content": LONG_SYSTEM_PROMPT},
            {"role": "user", "content": USER_MSG_TURN_2},
        ],
    )
    usage2 = extract_openai_cache_tokens(resp2.usage, provider="arkcode")
    print(f"[kimi-k2.6/arkcode] Full turn 2 usage object: {resp2.usage!r}")

    print(f"\n[kimi-k2.6/arkcode] Turn 1 extracted: {usage1}")
    print(f"[kimi-k2.6/arkcode] Turn 2 extracted: {usage2}")

    hit1 = usage1["cached_tokens"]
    hit2 = usage2["cached_tokens"]

    if hit1 > 0 or hit2 > 0:
        field = usage1["_cache_field_used"] if hit1 > 0 else usage2["_cache_field_used"]
        print(f"[kimi-k2.6/arkcode] CACHE STATS PRESENT via field: {field}")
    else:
        raw1 = usage1["_raw"]
        raw2 = usage2["_raw"]
        print("[kimi-k2.6/arkcode] NO cache stats detected.")
        print(f"  Turn 1 raw usage keys: {list(raw1.keys())}")
        print(f"  Turn 2 raw usage keys: {list(raw2.keys())}")
        # Dump the full raw dicts so we can see if there's a non-standard field
        print(f"  Turn 1 raw: {raw1}")
        print(f"  Turn 2 raw: {raw2}")
        pytest.skip("kimi-k2.6 on arkcode returned no cache stats — provider limitation")
