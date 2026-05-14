"""Summarizer — runs a cheap LLM to produce / update a CompactionSummary."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage


@dataclass
class CompactionSummary:
    """Persisted running summary of a conversation's older turns.

    Stored on cubepi ``ctx.extra["compaction"]`` between turns. Three-field
    shape mirrors the canonical "running summary" pattern: the text, which
    messages it covers, and where the rolling window currently ends.
    """

    summary: str
    summarized_message_ids: list[str] = field(default_factory=list)
    last_summarized_message_id: str | None = None


SUMMARIZER_SYSTEM_PROMPT = """\
You compress a chat transcript into a brief, faithful narrative for an AI assistant
that is continuing the conversation. Rules:

1. Preserve facts, user goals, decisions made, and unresolved questions.
2. Preserve every 【N-K】 citation marker verbatim. Do not renumber, merge, or drop them.
3. Do not quote long tool outputs. Reference them by their citation markers instead.
4. Keep the language of the original conversation.
5. Output the summary directly. No preamble, no JSON, no markdown headers.
"""

EXISTING_SUMMARY_SUFFIX = """\
A previous summary already covers earlier turns:

<previous_summary>
{prev}
</previous_summary>

Merge it with the new turns below. Output the updated summary."""


def _format_messages_for_summary(messages: list[AnyMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.__class__.__name__.removesuffix("Message").lower() or "msg"
        content = m.content if isinstance(m.content, str) else str(m.content)
        parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)


async def summarize(
    *,
    model: BaseChatModel,
    messages_to_summarize: list[AnyMessage],
    existing: CompactionSummary | None,
    max_summary_tokens: int = 1024,
) -> CompactionSummary:
    """Generate or update a CompactionSummary covering messages_to_summarize."""
    system_text = SUMMARIZER_SYSTEM_PROMPT
    if existing and existing.summary:
        system_text = system_text + "\n\n" + EXISTING_SUMMARY_SUFFIX.format(prev=existing.summary)

    prompt_messages: list[AnyMessage] = [
        SystemMessage(content=system_text),
        HumanMessage(content=_format_messages_for_summary(messages_to_summarize)),
    ]
    bound = model.bind(max_tokens=max_summary_tokens)
    response = await bound.ainvoke(prompt_messages)
    text = response.content if isinstance(response.content, str) else str(response.content)

    new_ids: list[str] = [getattr(m, "id", None) or "" for m in messages_to_summarize]
    new_ids = [i for i in new_ids if i]
    prior_ids: list[str] = list(existing.summarized_message_ids) if existing else []

    return CompactionSummary(
        summary=text.strip(),
        summarized_message_ids=prior_ids + new_ids,
        last_summarized_message_id=(
            new_ids[-1] if new_ids else (existing.last_summarized_message_id if existing else None)
        ),
    )
