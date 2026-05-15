"""E2E: a single conversation that walks through the full middleware stack.

Turn 1 → timestamps, cost, calculator (builtin tool).
Turn 2 → todo (write_todos tool).
Turn 3 → sandbox (execute tool).
Turn 4 → memory_save (builtin tool backed by MemoryService).
Loaded-but-quiet middleware (skills/citation/attachments/artifacts) covered
by zero-error assertions. MemoryMiddleware's read path (transform_context
injection) runs implicitly on every turn. See design doc 2026-05-15.
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
    TOOL_MEMORY_SAVE,
    TOOL_SANDBOX,
    TOOL_TODO,
    assistant_text,
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
    tool_result_contents,
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
    t1_results = tool_result_contents(t1)
    assert any("304" in c for c in t1_results), (
        f"expected '304' in calculator tool_result; got: {t1_results}"
    )
    t1_text = assistant_text(t1)
    assert "304" in t1_text, f"expected '304' in assistant reply; got: {t1_text!r}"

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
    t2_results = tool_result_contents(t2)
    assert len(t2_results) >= 1, f"expected at least one tool_result in t2; got: {t2_results}"
    assert any(c.strip() for c in t2_results), (
        f"expected non-empty tool_result content in t2; got: {t2_results}"
    )
    t2_text = assistant_text(t2)
    assert t2_text.strip() != "", f"expected non-empty assistant reply in t2; got: {t2_text!r}"

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
    t3_results = tool_result_contents(t3)
    assert any("55" in c for c in t3_results), (
        f"expected '55' in execute tool_result; got: {t3_results}"
    )
    t3_text = assistant_text(t3)
    assert "55" in t3_text, f"expected '55' in assistant reply; got: {t3_text!r}"

    # Turn 4: memory_save → builtin memory tool
    t4 = await post_turn(
        client,
        ws_id,
        conv_id,
        "你必须用 memory_save 工具把我的偏好记下来。参数：scope='personal'，"
        "type='preference'，content='我做数据处理偏好用 Python + pandas'。"
        "保存成功后用一句话确认。",
    )
    assert events_of_type(t4, EVT_ERROR) == [], f"errors in t4: {t4}"
    assert t4[-1].get("type") == EVT_DONE
    t4_tools = tool_call_names(t4)
    assert TOOL_MEMORY_SAVE in t4_tools, f"expected {TOOL_MEMORY_SAVE} call, got {t4_tools}"
    # memory_save returns JSON like {"status": "saved", "memory_id": "..."}
    t4_results = tool_result_contents(t4)
    assert any("saved" in c for c in t4_results), (
        f"expected 'saved' in memory_save tool_result; got: {t4_results}"
    )
    t4_text = assistant_text(t4)
    assert t4_text.strip() != "", f"expected non-empty assistant reply in t4; got: {t4_text!r}"

    # Whole-conversation union check
    all_types = {e.get("type") for e in (t1 + t2 + t3 + t4)}
    assert {EVT_TEXT_DELTA, EVT_TOOL_CALL, EVT_TOOL_RESULT, EVT_USAGE, EVT_DONE} <= all_types
