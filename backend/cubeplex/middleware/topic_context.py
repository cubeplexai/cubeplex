"""TopicContextMiddleware — injects conversation topic context into UserMessages.

Design & Cache-Discipline Contract:
- Zero mutations to BASE_SYSTEM_PROMPT / effective_system_prompt.
- Dynamic injection occurs exclusively within UserMessages in transform_context.
- Reads `metadata["topic_context"]` from UserMessage.
- Idempotent and deterministic: past turns replay identically from their
  stored metadata, ensuring prompt-cache hit stability across conversation turns.
"""

from __future__ import annotations

import asyncio

from cubeloop.agent.types import AgentContext
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import Message, TextContent, UserMessage

from cubeplex.agents.topic_context import render_conversation_context


def _augment_with_topic_context(
    msg: UserMessage,
    topic_id: str,
    topic_title: str,
) -> UserMessage:
    """Return a fresh UserMessage with <conversation_context ... /> prepended.

    Preserves message identity (id, run_id, timestamp) via model_copy so
    compaction state remains stable across turns.
    """
    rendered = render_conversation_context(topic_id, topic_title)
    if not rendered:
        return msg

    new_content = list(msg.content)
    prepended = False
    for i in range(len(new_content)):
        item = new_content[i]
        if isinstance(item, TextContent):
            # Idempotency guard: do not re-inject if already present
            if item.text.startswith("<conversation_context"):
                return msg
            new_content[i] = TextContent(text=rendered + "\n\n" + item.text)
            prepended = True
            break
    if not prepended:
        new_content.insert(0, TextContent(text=rendered))

    return msg.model_copy(update={"content": new_content, "metadata": dict(msg.metadata)})


class TopicContextMiddleware(Middleware):
    """Injects <conversation_context ... /> into UserMessages for topic conversations."""

    async def transform_context(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> list[Message]:
        del ctx, signal
        out: list[Message] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                raw_topic = msg.metadata.get("topic_context") if msg.metadata else None
                if isinstance(raw_topic, dict):
                    topic_id = raw_topic.get("topic_id")
                    topic_title = raw_topic.get("topic_title")
                    if isinstance(topic_id, str) and isinstance(topic_title, str):
                        msg = _augment_with_topic_context(msg, topic_id, topic_title)
            out.append(msg)
        return out
