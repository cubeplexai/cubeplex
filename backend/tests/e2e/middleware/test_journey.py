"""E2E: a single conversation that walks through the full middleware stack.

Turn 1 → timestamps, cost, calculator (builtin tool).
Turn 2 → todo (write_todos tool).
Turn 3 → sandbox (execute tool).
Loaded-but-quiet middleware (memory/skills/citation/attachments/artifacts)
covered by zero-error assertions. See design doc 2026-05-15.
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    EVT_TEXT_DELTA,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    EVT_USAGE,
    TOOL_CALCULATOR,
    TOOL_SANDBOX,
    TOOL_TODO,
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_full_middleware_journey(member_client: tuple) -> None:  # type: ignore[type-arg]
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "middleware journey")

    # Turn 1: time + arithmetic → calculator, timestamps, cost
    t1 = await post_turn(
        client,
        ws_id,
        conv_id,
        "现在几点？顺便你必须先调用 calculator 工具算 (2025 - 1949) * 4。",
    )
    assert events_of_type(t1, EVT_ERROR) == [], f"errors in t1: {t1}"
    assert t1[-1].get("type") == EVT_DONE
    assert events_of_type(t1, EVT_USAGE), "no usage event (cost middleware silent)"
    t1_tools = tool_call_names(t1)
    assert TOOL_CALCULATOR in t1_tools, f"expected calculator call, got {t1_tools}"

    # Turn 2: todo list → write_todos
    t2 = await post_turn(
        client,
        ws_id,
        conv_id,
        "把刚才解题的步骤你必须用 write_todos 工具整理成一个列表，每个步骤一条。",
    )
    assert events_of_type(t2, EVT_ERROR) == []
    assert t2[-1].get("type") == EVT_DONE
    t2_tools = tool_call_names(t2)
    assert TOOL_TODO in t2_tools, f"expected {TOOL_TODO} call, got {t2_tools}"

    # Turn 3: sandbox → execute
    t3 = await post_turn(
        client,
        ws_id,
        conv_id,
        "你必须用 execute 工具运行一段 Python：print(sum(range(11)))，告诉我结果。",
    )
    assert events_of_type(t3, EVT_ERROR) == []
    assert t3[-1].get("type") == EVT_DONE
    t3_tools = tool_call_names(t3)
    assert TOOL_SANDBOX in t3_tools, f"expected {TOOL_SANDBOX} call, got {t3_tools}"

    # Whole-conversation union check
    all_types = {e.get("type") for e in (t1 + t2 + t3)}
    assert {EVT_TEXT_DELTA, EVT_TOOL_CALL, EVT_TOOL_RESULT, EVT_USAGE, EVT_DONE} <= all_types
