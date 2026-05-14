"""Approximate token counting for cubepi messages.

IMPORTANT: callers must pass the view they intend to send to the LLM
(i.e. the post-compaction projection [summary, *recent]), NOT the raw
message history. Passing raw history breaks scaling accuracy because
historical AssistantMessage.usage reflects the compressed view the
LLM actually saw — comparing it against an approx walked over the full
history yields a scale_factor < 1 (clamped to 1.0, scaling disabled).
"""

from __future__ import annotations

import json

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

# 2.0 chars/token is a deliberate conservative override of the 4.0 default
# used by langchain_core. 4.0 underestimates Chinese / CJK by 3-4x; with
# our threshold of context_window * 0.7, underestimating means compacting
# too late → overflow. Once usage scaling kicks in (turn 2+), the value
# self-corrects — this just protects the cold start.
_CHARS_PER_TOKEN = 2.0

# Minimum input_tokens before we trust usage metadata for scaling.
_SCALE_MIN_TOKENS = 100


def approx_tokens(messages: list[Message]) -> int:
    """Approximate total tokens for a list of cubepi messages.

    For AssistantMessages with ``usage.input_tokens >= _SCALE_MIN_TOKENS``,
    derives a chars-per-token scale factor (clamped to [1.0, 1.25]) so
    historical real token counts auto-calibrate the estimate.
    """
    if not messages:
        return 0

    total_chars = 0
    scale_factor: float | None = None

    for msg in messages:
        if isinstance(msg, UserMessage):
            for user_block in msg.content:
                if isinstance(user_block, TextContent):
                    total_chars += len(user_block.text)
        elif isinstance(msg, AssistantMessage):
            for assistant_block in msg.content:
                if isinstance(assistant_block, TextContent):
                    total_chars += len(assistant_block.text)
                elif isinstance(assistant_block, ToolCall):
                    total_chars += len(json.dumps(assistant_block.arguments or {}))
            usage = msg.usage
            if usage and usage.input_tokens >= _SCALE_MIN_TOKENS and scale_factor is None:
                chars_estimate = usage.input_tokens * _CHARS_PER_TOKEN
                if chars_estimate > 0:
                    raw_factor = total_chars / chars_estimate
                    scale_factor = max(1.0, min(raw_factor, 1.25))
        elif isinstance(msg, ToolResultMessage):
            for tool_block in msg.content:
                if isinstance(tool_block, TextContent):
                    total_chars += len(tool_block.text)

    char_estimate = total_chars / _CHARS_PER_TOKEN
    if scale_factor is not None:
        return int(char_estimate * scale_factor)
    return int(char_estimate)
