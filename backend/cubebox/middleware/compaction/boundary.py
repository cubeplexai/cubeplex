"""Boundary selection for compaction — picks a safe split point.

Operates on cubepi message types from cubepi.providers.base.
"""

from __future__ import annotations

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def safe_boundary(
    messages: list[Message],
    *,
    keep_recent: int,
    min_compact: int = 1,
) -> int | None:
    """Return an index `b` such that messages[:b] is summarizable and messages[b:] is kept.

    Constraints:
      1. messages[b:] must contain >= keep_recent items.
      2. messages[b] must be a UserMessage (start of a turn).
      3. messages[b:] must not contain a ToolResultMessage whose tool_call_id
         has no matching ToolCall in an AssistantMessage within messages[b:].
      4. If no boundary satisfies all and leaves at least min_compact messages
         in the prefix, return None (caller skips compaction this round).
    """
    n = len(messages)
    if n <= keep_recent:
        return None

    candidate = n - keep_recent
    while candidate > 0:
        msg = messages[candidate]
        if not isinstance(msg, UserMessage):
            candidate -= 1
            continue
        if not _suffix_is_self_contained(messages[candidate:]):
            candidate -= 1
            continue
        if candidate < min_compact:
            return None
        return candidate

    return None


def _suffix_is_self_contained(suffix: list[Message]) -> bool:
    """Every ToolResultMessage in the suffix must have its parent ToolCall in the suffix."""
    available_call_ids: set[str] = set()
    for msg in suffix:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolCall) and block.id:
                    available_call_ids.add(block.id)
        elif isinstance(msg, ToolResultMessage):
            if msg.tool_call_id and msg.tool_call_id not in available_call_ids:
                return False
    return True
