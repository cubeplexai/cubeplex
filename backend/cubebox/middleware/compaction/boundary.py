"""Boundary selection for compaction — picks a safe split point."""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


def safe_boundary(
    messages: list[AnyMessage],
    *,
    keep_recent: int,
    min_compact: int = 1,
) -> int | None:
    """Return an index `b` such that messages[:b] is summarizable and messages[b:] is kept.

    Constraints:
      1. messages[b:] must contain >= keep_recent items.
      2. messages[b] must be a HumanMessage (start of a turn).
      3. messages[b:] must not contain a ToolMessage whose tool_call_id has no
         matching AIMessage.tool_calls within messages[b:].
      4. If no boundary satisfies all and leaves at least min_compact messages
         in the prefix, return None (caller skips compaction this round).
    """
    n = len(messages)
    if n <= keep_recent:
        return None

    candidate = n - keep_recent
    while candidate > 0:
        msg = messages[candidate]
        if not isinstance(msg, HumanMessage):
            candidate -= 1
            continue
        if not _suffix_is_self_contained(messages[candidate:]):
            candidate -= 1
            continue
        if candidate < min_compact:
            return None
        return candidate

    return None


def _suffix_is_self_contained(suffix: list[AnyMessage]) -> bool:
    """Every ToolMessage in the suffix must have its parent AIMessage in the suffix."""
    available_call_ids: set[str] = set()
    for msg in suffix:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    available_call_ids.add(tc_id)
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id and msg.tool_call_id not in available_call_ids:
                return False
    return True
