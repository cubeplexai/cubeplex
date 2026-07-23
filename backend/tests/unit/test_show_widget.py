"""show_widget builtin tool + subagent-scope helper — unit tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cubeplex.prompts.widget import WIDGET_GUIDELINES, WIDGET_TOOL_DESCRIPTION
from cubeplex.skills.frontmatter import parse_skill_md
from cubeplex.streams.run_manager import _subagent_shared_tools
from cubeplex.tools.builtin.show_widget import _ShowWidgetArgs, make_show_widget_tool

_PREINSTALLED = (
    Path(__file__).resolve().parents[2] / "skills" / "preinstalled" / "show-widget" / "SKILL.md"
)


def test_tool_metadata() -> None:
    tool = make_show_widget_tool()
    assert tool.name == "show_widget"
    assert tool.parameters is _ShowWidgetArgs
    assert "show-widget" in tool.description


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


def test_always_on_guidelines_are_short_stub() -> None:
    """F1: bulk playbook moved to skill; system prefix keeps only hard limits."""
    assert "show_widget" in WIDGET_GUIDELINES
    assert "fetch" in WIDGET_GUIDELINES
    assert "localStorage" in WIDGET_GUIDELINES
    assert "show-widget" in WIDGET_GUIDELINES  # points agents at the skill
    # Stay well under the old ~11k always-on block.
    assert len(WIDGET_GUIDELINES) < 1500
    assert "MutationObserver" not in WIDGET_GUIDELINES  # skeleton detail → skill
    assert "show-widget" in WIDGET_TOOL_DESCRIPTION


def test_preinstalled_show_widget_skill_exists_and_parses() -> None:
    assert _PREINSTALLED.is_file()
    text = _PREINSTALLED.read_text(encoding="utf-8")
    fm = parse_skill_md(text)
    assert fm.name == "show-widget"
    assert fm.version
    assert "show_widget" in fm.description or "widget" in fm.description.lower()
    # Playbook content lives in the skill body.
    assert "Chart.js" in text
    assert "MutationObserver" in text
    assert "var(--fg)" in text
    assert "context-stroke" in text


def test_subagent_shared_tools_drops_show_widget() -> None:
    sw = make_show_widget_tool()
    keep = SimpleNamespace(name="execute")  # helper only reads .name
    result = _subagent_shared_tools([sw, keep])  # type: ignore[list-item]
    assert sw not in result  # show_widget removed
    assert keep in result  # other tools retained
