"""E2E: explicit subagent dispatch.

Confirms the subagents middleware registers a `subagent` spawn tool and
the agent actually calls it under a real LLM. See design doc 2026-05-15.
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    TOOL_SUBAGENT_SPAWN,
    assistant_text,
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
    tool_result_contents,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_subagent_dispatch_real_llm(member_client: tuple) -> None:  # type: ignore[type-arg]
    """If qwen3.6-flash consistently refuses to spawn, convert to
    @pytest.mark.xfail(strict=False, reason=...). Do not soften the
    assertion to 'any tool'. We want visibility, not green-but-uncovered.
    """
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "subagent dispatch")

    events = await post_turn(
        client,
        ws_id,
        conv_id,
        "请用 subagent 工具派一个子代理去帮我总结一句话：'cubeplex 是什么'，"
        "你只负责派单和汇总，不要自己回答。",
    )

    assert events_of_type(events, EVT_ERROR) == [], f"errors: {events}"
    assert events[-1].get("type") == EVT_DONE
    tools = tool_call_names(events)
    assert TOOL_SUBAGENT_SPAWN in tools, f"expected {TOOL_SUBAGENT_SPAWN} call, got {tools}"

    results = tool_result_contents(events)
    assert any(r.strip() for r in results), f"all tool_result contents empty: {results!r}"
    assert any("cubeplex" in r.lower() for r in results), (
        f"expected 'cubeplex' in some tool_result; got: {results!r}"
    )
    outer_text = assistant_text(events)
    assert outer_text.strip() != "", "outer agent produced no text reply"
    assert "cubeplex" in outer_text.lower(), (
        f"expected 'cubeplex' in outer reply; got: {outer_text!r}"
    )
