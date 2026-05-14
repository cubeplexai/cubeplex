"""Summarizer — runs a cheap cubepi Provider to produce / update a CompactionSummary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from cubepi.providers.base import (
    Message,
    TextContent,
    ToolCall,
    UserMessage,
)


@dataclass
class CompactionSummary:
    """Persisted running summary of a conversation's older turns.

    Stored on cubepi ``ctx.extra["compaction"]`` between turns. The
    three-field shape is preserved for checkpoint compatibility so existing
    checkpoints round-trip. ``summarized_message_ids`` is effectively empty
    in practice (cubepi messages don't carry an explicit id field) — the
    field is retained to keep serialized state stable.
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


class _OneShotProvider(Protocol):
    """Subset of cubepi.Provider needed by the summarizer.

    A real ``cubepi.Provider`` does not expose a single-shot text-generation
    method directly — the adapter wired by the middleware (see Task 2.4 /
    ``cubebox/llm/oneshot.py``) accumulates ``stream(...)`` deltas into a
    string and satisfies this Protocol. Test fakes can simply implement
    ``generate_once`` as an ``async def`` returning a fixed string.
    """

    async def generate_once(
        self,
        *,
        system: str,
        messages: list[Message],
        max_output_tokens: int,
    ) -> str: ...


def _format_message_for_summary(msg: Message) -> str:
    role = msg.__class__.__name__.removesuffix("Message").lower() or "msg"
    parts: list[str] = []
    for block in getattr(msg, "content", []):
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ToolCall):
            parts.append(f"[tool_call:{block.name}]")
        elif hasattr(block, "text"):
            # ImageContent or unknown text-bearing block; best-effort include.
            parts.append(str(getattr(block, "text", "")))
    return f"[{role}] " + " ".join(parts)


def _format_transcript(messages: list[Message]) -> str:
    return "\n\n".join(_format_message_for_summary(m) for m in messages)


async def summarize(
    *,
    provider: _OneShotProvider,
    messages_to_summarize: list[Message],
    existing: CompactionSummary | None,
    max_summary_tokens: int = 1024,
) -> CompactionSummary:
    """Generate or update a CompactionSummary covering messages_to_summarize."""
    system_text = SUMMARIZER_SYSTEM_PROMPT
    if existing and existing.summary:
        system_text = system_text + "\n\n" + EXISTING_SUMMARY_SUFFIX.format(prev=existing.summary)

    transcript = _format_transcript(messages_to_summarize)
    prompt: list[Message] = [UserMessage(content=[TextContent(text=transcript)])]

    text = await provider.generate_once(
        system=system_text,
        messages=prompt,
        max_output_tokens=max_summary_tokens,
    )

    # cubepi messages carry no explicit `.id` attribute; new_ids stays empty.
    new_ids = [str(getattr(m, "id", "") or "") for m in messages_to_summarize]
    new_ids = [i for i in new_ids if i]
    prior_ids: list[str] = list(existing.summarized_message_ids) if existing else []

    return CompactionSummary(
        summary=text.strip(),
        summarized_message_ids=prior_ids + new_ids,
        last_summarized_message_id=(
            new_ids[-1] if new_ids else (existing.last_summarized_message_id if existing else None)
        ),
    )
