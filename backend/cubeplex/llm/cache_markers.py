"""cubeplex-side CacheMarkerPolicy implementation for cubepi.AnthropicProvider.

Walks back through the message list to find the most recent completed
AssistantMessage and marks it. The system prompt and last tool definition
also get markers (cubeplex's prompt cache discipline; see backend/docs/prompt-cache-discipline.md).
"""

from __future__ import annotations

from cubepi.providers.base import AssistantMessage, Message


class CubeplexCacheMarkerPolicy:
    """Policy: mark system + last completed AssistantMessage + last tool.

    "Completed" here means: any AssistantMessage in the messages list.
    cubeplex builds the request after the assistant has finished streaming,
    so every AssistantMessage in the list is by definition completed.
    """

    def mark_system(self) -> bool:
        return True

    def mark_last_tool(self) -> bool:
        return True

    def message_breakpoint_indices(self, messages: list[Message]) -> list[int]:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AssistantMessage):
                return [i]
        return []
