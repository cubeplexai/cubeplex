"""CubePlex-specific compaction helpers."""

from __future__ import annotations

import json

from cubepi.providers.base import TextContent, ToolResultMessage


def compact_loaded_skill(message: ToolResultMessage) -> str | None:
    """Preserve only the identity needed to reload a compacted skill."""
    if message.tool_name != "load_skill" or message.is_error:
        return None

    text = "\n".join(block.text for block in message.content if isinstance(block, TextContent))
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("loaded") is not True:
        return None

    skill_name = payload.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return None
    return (
        f"Previously loaded skill: {skill_name.strip()} "
        "(full instructions omitted during compaction)."
    )
