"""show_widget builtin tool + subagent-scope helper — unit tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubeplex.prompts.widget import WIDGET_GUIDELINES
from cubeplex.streams.run_manager import _subagent_shared_tools
from cubeplex.tools.builtin.show_widget import _ShowWidgetArgs, make_show_widget_tool


def test_tool_metadata() -> None:
    tool = make_show_widget_tool()
    assert tool.name == "show_widget"
    assert tool.parameters is _ShowWidgetArgs


@pytest.mark.asyncio
async def test_execute_returns_light_ack() -> None:
    tool = make_show_widget_tool()
    result = await tool.execute(
        "call_1",
        _ShowWidgetArgs(title="demo", widget_code="<div>hi</div>"),
        signal=None,
        on_update=None,
    )
    text = "".join(c.text for c in result.content)
    assert "rendered" in text.lower()


def test_guidelines_mention_tool_and_constraints() -> None:
    assert "show_widget" in WIDGET_GUIDELINES
    assert "fetch" in WIDGET_GUIDELINES  # network-blocked note
    assert "localStorage" in WIDGET_GUIDELINES


def test_subagent_shared_tools_drops_show_widget() -> None:
    sw = make_show_widget_tool()
    keep = SimpleNamespace(name="execute")  # helper only reads .name
    result = _subagent_shared_tools([sw, keep])  # type: ignore[list-item]
    assert sw not in result  # show_widget removed
    assert keep in result  # other tools retained
