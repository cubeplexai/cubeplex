"""Tests for fold_event handling tool_call/tool_result events.

Field names follow the Task 0 audit:
- tool_call.data: {tool_call_id, name, arguments: JSON-string}
- tool_result.data: {tool_call_id, name, content, is_error, details}
- event.agent_id: top-level; non-null means sub-agent.
"""

import json

from cubebox.im.outbound import fold_event
from cubebox.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubebox", run_id="run_1")
    s.card_id = "AAQA"
    return s


def test_tool_call_appends_running_step_and_emits_patch() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "tool_call",
            "data": {
                "tool_call_id": "tc_1",
                "name": "bash",
                "arguments": json.dumps({"cmd": "ls"}),
            },
        },
        state,
        now=0.0,
    )
    assert op is not None
    # tool_call bypasses patch throttle on first appearance.
    assert op.kind == "patch_card"
    assert len(state.card_state.tool_steps) == 1
    step = state.card_state.tool_steps[0]
    assert step.status == "running"
    assert step.args == {"cmd": "ls"}
    assert step.start_monotonic == 0.0


def test_tool_call_with_empty_arguments_string() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_x", "name": "ping", "arguments": ""},
        },
        state,
        now=0.0,
    )
    assert state.card_state.tool_steps[0].args == {}


def test_tool_call_with_malformed_arguments_keeps_raw_string() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_x", "name": "weird", "arguments": "{not json"},
        },
        state,
        now=0.0,
    )
    # Fallback to a 1-key dict so rendering still has something to summarize.
    step = state.card_state.tool_steps[0]
    assert "raw" in step.args
    assert step.args["raw"] == "{not json"


def test_tool_result_marks_step_succeeded() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "tool_call",
            "data": {
                "tool_call_id": "tc_1",
                "name": "bash",
                "arguments": json.dumps({"cmd": "ls"}),
            },
        },
        state,
        now=0.0,
    )
    op = fold_event(
        {
            "type": "tool_result",
            "data": {
                "tool_call_id": "tc_1",
                "name": "bash",
                "content": "ok",
                "is_error": False,
                "details": None,
            },
        },
        state,
        now=10.0,
    )
    step = state.card_state.tool_steps[0]
    assert step.status == "succeeded"
    assert step.result == "ok"
    # elapsed_ms is now - start_monotonic in ms (10s → 10000ms).
    assert step.elapsed_ms == 10000
    assert op is not None and op.kind == "patch_card"


def test_tool_result_error_marks_failed_and_keeps_message() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "tool_call",
            "data": {
                "tool_call_id": "tc_2",
                "name": "bash",
                "arguments": "{}",
            },
        },
        state,
        now=0.0,
    )
    fold_event(
        {
            "type": "tool_result",
            "data": {
                "tool_call_id": "tc_2",
                "name": "bash",
                "content": "permission denied",
                "is_error": True,
                "details": None,
            },
        },
        state,
        now=0.5,
    )
    step = state.card_state.tool_steps[0]
    assert step.status == "failed"
    assert step.error == "permission denied"
    assert step.elapsed_ms == 500


def test_tool_call_before_card_emits_card_create() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    # state.card_id is None — no card yet
    op = fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_1", "name": "bash", "arguments": "{}"},
        },
        state,
        now=0.0,
    )
    assert op is not None
    assert op.kind == "card_create"
    assert len(state.card_state.tool_steps) == 1


def test_sub_agent_tool_call_routes_to_sub_agent_row_not_tool_steps() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "tool_call",
            "data": {
                "tool_call_id": "tc_sub_1",
                "name": "read_file",
                "arguments": "{}",
            },
            "agent_id": "subagent:parent_tc_1",
            "agent_name": "researcher",
        },
        state,
        now=0.0,
    )
    assert op is not None and op.kind == "patch_card"
    # The sub-agent tool call did NOT go into main tool_steps.
    assert state.card_state.tool_steps == []
    # It DID create / increment a SubAgentRow.
    assert len(state.card_state.sub_agents) == 1
    row = state.card_state.sub_agents[0]
    assert row.agent_id == "subagent:parent_tc_1"
    assert row.name == "researcher"
    assert row.tool_count == 1


def test_sub_agent_second_tool_call_increments_existing_row() -> None:
    state = _state_with_card()
    common = {"agent_id": "subagent:p", "agent_name": "r"}
    fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_a", "name": "x", "arguments": "{}"},
            **common,
        },
        state,
        now=0.0,
    )
    fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_b", "name": "y", "arguments": "{}"},
            **common,
        },
        state,
        now=0.5,
    )
    assert len(state.card_state.sub_agents) == 1
    assert state.card_state.sub_agents[0].tool_count == 2


def test_sub_agent_tool_result_does_not_touch_main_tool_steps() -> None:
    state = _state_with_card()
    common = {"agent_id": "subagent:p", "agent_name": "r"}
    fold_event(
        {
            "type": "tool_call",
            "data": {"tool_call_id": "tc_a", "name": "x", "arguments": "{}"},
            **common,
        },
        state,
        now=0.0,
    )
    # Sub-agent tool_result: simply no-op for v1 (we don't track sub-agent step status).
    fold_event(
        {
            "type": "tool_result",
            "data": {
                "tool_call_id": "tc_a",
                "name": "x",
                "content": "ok",
                "is_error": False,
                "details": None,
            },
            **common,
        },
        state,
        now=0.2,
    )
    assert state.card_state.tool_steps == []
