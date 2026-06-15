"""Verify shared card_model exports match the original feishu card_model."""

from __future__ import annotations

from cubebox.im.card_model import (
    CardState,
    ToolStep,
)


def test_card_state_basic() -> None:
    cs = CardState(bot_name="test", run_id="r1")
    assert cs.streaming_content == ""
    assert cs.tool_steps == []
    assert cs.finalized is False


def test_tool_step_lifecycle() -> None:
    ts = ToolStep(id="t1", name="search", args={"q": "hello"})
    assert ts.status == "running"
    ts.mark_succeeded(result="ok", elapsed_ms=100)
    assert ts.status == "succeeded"
    assert ts.elapsed_ms == 100


def test_card_state_find_tool() -> None:
    cs = CardState(bot_name="test", run_id="r1")
    cs.tool_steps.append(ToolStep(id="t1", name="search", args={}))
    assert cs.find_tool("t1") is not None
    assert cs.find_tool("t2") is None


def test_feishu_reexport() -> None:
    """The feishu module must still re-export the same classes."""
    from cubebox.im.feishu.card_model import CardState as FeishuCardState
    from cubebox.im.feishu.card_model import ToolStep as FeishuToolStep

    assert FeishuCardState is CardState
    assert FeishuToolStep is ToolStep
