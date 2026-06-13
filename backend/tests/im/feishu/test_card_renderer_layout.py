"""Tests for the whole-card render() — skeleton + element conditional inclusion."""

from cubebox.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    ToolStep,
)
from cubebox.im.feishu.card_renderer import render


def _empty_state() -> CardState:
    return CardState(bot_name="cubebox", run_id="run_1")


def test_empty_card_has_skeleton_and_no_panels() -> None:
    card = render(_empty_state())
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert card["config"]["update_multi"] is True

    element_ids = [e["element_id"] for e in card["body"]["elements"] if "element_id" in e]
    assert "streaming_content" in element_ids
    # Empty panels are NOT included.
    assert "tool_panel" not in element_ids
    assert "artifacts" not in element_ids
    assert "pending_input" not in element_ids


def test_streaming_content_uses_optimized_markdown() -> None:
    state = _empty_state()
    state.streaming_content = "# H1 title"
    card = render(state)
    streaming = next(
        e for e in card["body"]["elements"] if e.get("element_id") == "streaming_content"
    )
    assert streaming["content"].startswith("#### H1 title")


def test_tool_panel_renders_running_step() -> None:
    state = _empty_state()
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    title = panel["header"]["title"]["content"]
    assert "运行中" in title or "Running" in title


def test_tool_panel_renders_failed_step_with_red_badge() -> None:
    state = _empty_state()
    step = ToolStep(id="tc_1", name="bash", args={"cmd": "ls"})
    step.mark_failed(error="permission denied", elapsed_ms=20)
    state.tool_steps.append(step)
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    title = panel["header"]["title"]["content"]
    assert "失败" in title or "Failed" in title


def test_artifact_image_renders_img_element() -> None:
    state = _empty_state()
    state.artifacts.append(
        ArtifactItem(id="a", artifact_type="image", name="x.png", image_key="img_v1_abc")
    )
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "artifacts")
    assert any("img" in str(item).lower() for item in panel["elements"])


def test_artifact_link_renders_button() -> None:
    state = _empty_state()
    state.artifacts.append(
        ArtifactItem(id="a", artifact_type="document", name="r.pdf", share_url="https://x/y")
    )
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "artifacts")
    serialized = str(panel)
    assert "button" in serialized
    assert "https://x/y" in serialized


def test_pending_input_renders_buttons_with_payload() -> None:
    state = _empty_state()
    state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run_1",
        question="Continue?",
        choices=[("yes", "primary"), ("no", "default")],
        question_id="q_1",
        answer_key="approve_deploy",
    )
    card = render(state)
    container = next(e for e in card["body"]["elements"] if e.get("element_id") == "pending_input")
    s = str(container)
    assert "yes" in s and "no" in s
    assert "run_1" in s
    # button.value carries question_id so the resume call matches cubepi's
    # pending side, and answer_key so the answer dict has the right shape.
    assert "q_1" in s
    assert "approve_deploy" in s


def test_finalized_state_disables_streaming_mode() -> None:
    state = _empty_state()
    state.finalized = True
    card = render(state)
    assert card["config"]["streaming_mode"] is False


def test_error_state_uses_red_header() -> None:
    state = _empty_state()
    state.error = "boom"
    state.finalized = True
    card = render(state)
    assert card["header"]["template"] == "red"


def test_done_state_uses_green_header() -> None:
    state = _empty_state()
    state.streaming_content = "done"
    state.finalized = True
    state.error = None
    card = render(state)
    assert card["header"]["template"] == "green"


def test_sub_agent_row_rendered_above_tool_steps() -> None:
    from cubebox.im.feishu.card_model import SubAgentRow

    state = _empty_state()
    state.sub_agents.append(SubAgentRow(agent_id="subagent:tc_x", name="researcher", tool_count=3))
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    serialized = str(panel)
    assert "researcher" in serialized
    assert "3" in serialized
