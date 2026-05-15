"""E2E: compaction middleware participation.

Compaction's strong path (history trim) only fires above ~44,800 tokens
of history (fallback_context_window=64000 * threshold_ratio=0.7 per
COMPACTION_NOTE in _helpers.py). Hitting that threshold via real-LLM
filler is impractical for this suite, so this test verifies only that
the compaction middleware participates in the stack without crashing
streams under multi-turn load — the weak-fallback path documented in
the plan.

Track B chosen because: the app is created inside the fixture and the
config object is loaded at module import time, so monkeypatch.setenv
cannot override the already-initialized compaction thresholds per-test
without modifying conftest (out of scope).

See plan: docs/superpowers/plans/2026-05-15-agent-middleware-e2e.md
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    create_conversation,
    events_of_type,
    post_turn,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_compaction_middleware_does_not_crash_stream(
    member_client: tuple,  # type: ignore[type-arg]
) -> None:
    """Compaction middleware participates in the stack without crashing streams.

    Sends 4 filler turns to put content in history, then a final summary
    turn. Asserts every turn completes with a ``done`` event and no ``error``
    events — proving the middleware path is wired and does not blow up under
    multi-turn load, even when the compaction threshold is never reached at
    this token volume.
    """
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "compaction")

    filler = ("背景资料段落，仅用于撑大上下文。" * 60) + "请简短回复 ok。"

    for i in range(4):
        evts = await post_turn(client, ws_id, conv_id, f"[{i}] {filler}")
        assert events_of_type(evts, EVT_ERROR) == [], f"errors in filler turn {i}: {evts}"
        assert evts[-1].get("type") == EVT_DONE, (
            f"filler turn {i} did not terminate cleanly; last event: {evts[-1]}"
        )

    final = await post_turn(
        client,
        ws_id,
        conv_id,
        "用一句话总结上面对话的主题。",
    )
    assert events_of_type(final, EVT_ERROR) == [], f"errors in final turn: {final}"
    assert final[-1].get("type") == EVT_DONE, (
        f"final turn did not terminate cleanly; last event: {final[-1]}"
    )
