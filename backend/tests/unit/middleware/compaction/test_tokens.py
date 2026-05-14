"""Unit tests for approx_tokens (cubepi-native)."""

from __future__ import annotations

from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from cubebox.middleware.compaction.tokens import approx_tokens


def test_empty_returns_zero() -> None:
    assert approx_tokens([]) == 0


def test_text_chars_divided_by_chars_per_token() -> None:
    # 200 chars / 2.0 = 100 tokens
    msgs = [UserMessage(content=[TextContent(text="x" * 200)])]
    assert approx_tokens(msgs) == 100


def test_usage_metadata_scales_up_when_assistant_has_input_tokens() -> None:
    # 1000 chars across messages; assistant reports input_tokens=400.
    # raw_factor = 1000 / (400 * 2.0) = 1.25 → clamped to [1.0, 1.25] → 1.25.
    # final estimate = (1000 / 2.0) * 1.25 = 625.
    msgs = [
        UserMessage(content=[TextContent(text="x" * 900)]),
        AssistantMessage(
            content=[TextContent(text="y" * 100)],
            usage=Usage(input_tokens=400, output_tokens=10),
        ),
    ]
    assert approx_tokens(msgs) == 625


def test_tool_result_text_counted() -> None:
    msgs = [
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="t",
            content=[TextContent(text="x" * 200)],
        ),
    ]
    assert approx_tokens(msgs) == 100
