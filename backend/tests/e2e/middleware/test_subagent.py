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
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
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
        "请用 subagent 工具派一个子代理去帮我总结一句话：'cubebox 是什么'，"
        "你只负责派单和汇总，不要自己回答。",
    )

    assert events_of_type(events, EVT_ERROR) == [], f"errors: {events}"
    assert events[-1].get("type") == EVT_DONE
    tools = tool_call_names(events)
    assert TOOL_SUBAGENT_SPAWN in tools, f"expected {TOOL_SUBAGENT_SPAWN} call, got {tools}"
