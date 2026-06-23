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


def test_tool_panel_renders_before_streaming_content() -> None:
    """Reading order matches the chat flow: 'what was done → answer'.

    cubepi folds every text delta (intro + post-tool answer) into one
    streaming_content buffer, so placing tool_panel below the markdown
    would surface the model's final answer above the tools it ran to
    produce it. v1 fixes the visual oddity by emitting tool_panel first."""
    state = _empty_state()
    state.streaming_content = "result text"
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    ids_in_order = [e.get("element_id") for e in card["body"]["elements"]]
    assert ids_in_order.index("tool_panel") < ids_in_order.index("streaming_content")


def test_tool_step_omits_result_body() -> None:
    """v1 doesn't echo the tool result inside the card — the LLM's
    natural-language answer in streaming_content already summarizes it,
    so duplicating raw output just inflates the card and clashes with
    Feishu's 'request user to upgrade' fallback on long elements."""
    state = _empty_state()
    succeeded = ToolStep(id="tc_1", name="bash", args={"cmd": "ls"})
    succeeded.mark_succeeded(result='{"big":"json"}' * 100, elapsed_ms=42)
    state.tool_steps.append(succeeded)
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    # The full result body must not appear anywhere inside the panel.
    serialized = str(panel)
    assert '"big":"json"' not in serialized
    # But the tool name + args + duration are still present.
    assert "bash" in serialized
    assert "ls" in serialized
    assert "42ms" in serialized


def test_tool_panel_title_describes_tool_group_not_run_status() -> None:
    """Overall status lives in the top-of-body status element; the tool
    panel title only names the group + step count so the card doesn't show
    two "已完成" badges (one for the run, one for the tool subset)."""
    state = _empty_state()
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    title = panel["header"]["title"]["content"]
    assert "工具调用" in title
    assert "1 step" in title
    # Status words must NOT leak into the panel title — they belong to the
    # top status element.
    for status_word in ("运行中", "已完成", "失败", "Running", "Done", "Failed"):
        assert status_word not in title, f"unexpected {status_word!r} in {title!r}"


def test_tool_panel_failed_step_surfaces_red_badge_inline() -> None:
    """Failures are conveyed per-step with an inline ❌ badge, not via the
    panel title (which no longer carries run-status words)."""
    state = _empty_state()
    step = ToolStep(id="tc_1", name="bash", args={"cmd": "ls"})
    step.mark_failed(error="permission denied", elapsed_ms=20)
    state.tool_steps.append(step)
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    serialized = str(panel)
    assert "❌" in serialized
    assert "permission denied" in serialized


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
        choices=[("Yes", "yes", "primary"), ("No", "no", "default")],
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


def test_pending_input_button_text_is_label_value_carries_schema_key() -> None:
    """The button TEXT must be the human label; the button VALUE.choice must
    carry the schema value. Otherwise users pick between machine tokens.
    """
    state = _empty_state()
    state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run_X",
        question="Approve?",
        choices=[("批准", "approve", "primary"), ("拒绝", "deny", "danger")],
        question_id="q_X",
        answer_key="decision",
    )
    card = render(state)
    container = next(e for e in card["body"]["elements"] if e.get("element_id") == "pending_input")
    column_set = next(el for el in container["elements"] if el.get("tag") == "column_set")
    buttons = [col["elements"][0] for col in column_set["columns"]]
    # Button TEXT carries the human-readable Chinese labels.
    button_texts = [btn["text"]["content"] for btn in buttons]
    assert button_texts == ["批准", "拒绝"]
    # CardKit 2.0: callback payload lives INSIDE the callback behavior, NOT
    # as a sibling ``value``. With value at the wrong slot Feishu sends back
    # an empty action.value and the resume call fails.
    for btn in buttons:
        assert "value" not in btn, "value must be inside behaviors[0], not at button root"
        assert btn["behaviors"][0]["type"] == "callback"
    button_choices = [btn["behaviors"][0]["value"]["choice"] for btn in buttons]
    assert button_choices == ["approve", "deny"]


def test_finalized_state_disables_streaming_mode() -> None:
    state = _empty_state()
    state.finalized = True
    card = render(state)
    assert card["config"]["streaming_mode"] is False


def _status_content(card: dict) -> str:
    status = next(e for e in card["body"]["elements"] if e.get("element_id") == "status")
    return status["text"]["content"]


def test_error_state_status_is_red() -> None:
    state = _empty_state()
    state.error = "boom"
    state.finalized = True
    card = render(state)
    content = _status_content(card)
    assert "red" in content
    assert "运行失败" in content


def test_done_state_status_is_green() -> None:
    state = _empty_state()
    state.streaming_content = "done"
    state.finalized = True
    state.error = None
    card = render(state)
    content = _status_content(card)
    assert "green" in content
    assert "已完成" in content


def test_card_has_no_native_header() -> None:
    """Native Feishu card header renders large+bold; status moved into body
    as normal-size text so the message body owns the visual weight."""
    state = _empty_state()
    card = render(state)
    assert "header" not in card


def test_status_element_renders_before_other_body_elements() -> None:
    state = _empty_state()
    state.streaming_content = "answer"
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    ids = [e.get("element_id") for e in card["body"]["elements"]]
    assert ids[0] == "status"


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
