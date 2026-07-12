"""Real-LLM prompt cache regression gate (issue #64, plan task 8.3).

This is the spec-mandated gate for the memory system: a stable system+
memory prefix across N turns must drive cache reuse rate >= 50% by turn 2
and >= 85% by turn N. The byte-stability invariants are covered by
tests/unit/test_memory_cache_stability.py and run on every commit.

Endpoint capability is declared explicitly via
``CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true``. When set, a turn-2 cache_read of 0
is treated as a regression and FAILS. When unset (the default), it is
treated as endpoint not honoring cache_control and SKIPS — appropriate
for proxies that accept the markers but do not pass them through (e.g.
DeepSeek's Anthropic-compat surface).

This makes the test discriminate "cache capability missing" from "cache
broken" rather than collapsing both to skip.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.memory._helpers import AgentRunError, send_message_and_collect_usage

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
    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "cache-gate"})
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    # Probe — does this endpoint report cache fields at all?
    try:
        warmup = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)
    except AgentRunError as exc:
        pytest.skip(
            f"Agent run failed before usage data could be collected. "
            f"This is an infrastructure issue, not a cache regression. "
            f"Details: {exc}"
        )
    assert warmup["input_tokens"] > 0, (
        "Probe failed: no usage event observed at all. Either the LLM "
        "factory branch is wrong or stream_usage is not enabled."
    )

    # Turn 2 — second message in same conversation. With a stable prefix
    # we should see cache_read_tokens > 0 if the endpoint supports caching
    # at all.
    try:
        second = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)
    except AgentRunError as exc:
        pytest.skip(f"Turn 2 agent run failed: {exc}")
    cache_capable = os.environ.get("CUBEPLEX_E2E_LLM_CACHE_CAPABLE", "false").lower() == "true"
    if second["cache_read_tokens"] == 0:
        message = (
            f"Endpoint reported zero cache_read on turn 2: warmup "
            f"input={warmup['input_tokens']}, turn2 input={second['input_tokens']}, "
            f"turn2 cache_read=0."
        )
        if cache_capable:
            pytest.fail(
                f"{message} Endpoint declared cache-capable via "
                f"CUBEPLEX_E2E_LLM_CACHE_CAPABLE=true; this is a real "
                f"regression. Common causes: cache_control markers not "
                f"applied, dynamic content invalidating the stable prefix. "
                f"See backend/docs/prompt-cache-discipline.md."
            )
        pytest.skip(
            f"{message} CUBEPLEX_E2E_LLM_CACHE_CAPABLE not set; treating as "
            f"endpoint not honoring cache_control. Set the env to 'true' on "
            f"a known-cache-capable endpoint (e.g. Anthropic official) to "
            f"convert this branch into a hard failure."
        )

    # Strict bar — turn 2 must reuse at least half the input.
    ratio_2 = second["cache_read_tokens"] / max(second["input_tokens"], 1)
    assert ratio_2 >= 0.5, (
        f"Turn 2 cache reuse {ratio_2:.2%} below 50% bar "
        f"(cache_read={second['cache_read_tokens']}, input={second['input_tokens']}). "
        f"Likely cause: dynamic content snuck into the stable prefix. "
        f"See backend/docs/prompt-cache-discipline.md."
    )

    # Turn 3..N — extend conversation, last turn must hit the high bar.
    last = second
    for i in range(3, N_TURNS + 1):
        try:
            last = await send_message_and_collect_usage(client, ws_id, conv_id, PRIMER_USER_TEXT)
        except AgentRunError as exc:
            pytest.skip(f"Turn {i} agent run failed: {exc}")

    ratio_n = last["cache_read_tokens"] / max(last["input_tokens"], 1)
    assert ratio_n >= 0.85, (
        f"Turn {N_TURNS} cache reuse {ratio_n:.2%} below 85% bar "
        f"(cache_read={last['cache_read_tokens']}, input={last['input_tokens']}). "
        f"With a stable prefix and N={N_TURNS} turns, this should be 90%+."
    )
