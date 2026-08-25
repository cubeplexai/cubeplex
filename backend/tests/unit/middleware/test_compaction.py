"""Unit tests for CubePlex-specific compaction behavior."""

from __future__ import annotations

import json

from cubepi.middleware.compaction.pruner import prune_tool_results
from cubepi.providers.base import TextContent, ToolResultMessage

from cubeplex.middleware.compaction import compact_loaded_skill


def _result(payload: dict[str, object], *, is_error: bool = False) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id="tc-1",
        tool_name="load_skill",
        content=[TextContent(text=json.dumps(payload))],
        is_error=is_error,
    )


def test_compact_loaded_skill_preserves_name_without_content_or_version() -> None:
    compacted = compact_loaded_skill(
        _result(
            {
                "skill_name": "deep-research",
                "version": "2.0.0",
                "content": "# Deep Research\n\nFull private instructions",
                "loaded": True,
                "path": "/workspace/.skills/deep-research/2.0.0",
            }
        )
    )

    assert compacted == (
        "Previously loaded skill: deep-research (full instructions omitted during compaction)."
    )
    assert "2.0.0" not in compacted
    assert "Full private instructions" not in compacted


def test_compaction_pruner_keeps_only_reload_metadata() -> None:
    result = _result(
        {
            "skill_name": "deep-research",
            "version": "2.0.0",
            "content": "# Deep Research\n\nFull private instructions",
            "loaded": True,
        }
    )

    pruned, preserved = prune_tool_results(
        [result],
        tail_start=1,
        compressor=compact_loaded_skill,
    )

    assert preserved == {
        0: ("Previously loaded skill: deep-research (full instructions omitted during compaction).")
    }
    assert pruned[0].content == [TextContent(text="[load_skill] preserved")]


def test_compact_loaded_skill_ignores_failed_load() -> None:
    assert (
        compact_loaded_skill(
            _result(
                {"skill_name": "deep-research", "loaded": False},
                is_error=True,
            )
        )
        is None
    )


def test_compact_loaded_skill_ignores_other_tools_and_malformed_results() -> None:
    other = ToolResultMessage(
        tool_call_id="tc-2",
        tool_name="calculator",
        content=[TextContent(text='{"skill_name":"deep-research","loaded":true}')],
    )
    malformed = ToolResultMessage(
        tool_call_id="tc-3",
        tool_name="load_skill",
        content=[TextContent(text="not json")],
    )

    assert compact_loaded_skill(other) is None
    assert compact_loaded_skill(malformed) is None
