"""Unit tests for summarize() — uses cubepi Provider one-shot call."""

from __future__ import annotations

from typing import Any

import pytest
from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    UserMessage,
)

from cubebox.middleware.compaction.summarizer import (
    CompactionSummary,
    summarize,
)


class _FakeProvider:
    """cubepi-Provider-like fake: implements ``generate_once`` only."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.calls: list[dict[str, Any]] = []

    async def generate_once(
        self,
        *,
        system: str,
        messages: list[Message],
        max_output_tokens: int,
    ) -> str:
        self.calls.append(
            {"system": system, "messages": messages, "max_output_tokens": max_output_tokens}
        )
        return self.reply_text


@pytest.mark.asyncio
async def test_creates_new_summary_from_messages() -> None:
    provider = _FakeProvider("Compressed summary of the chat.")
    msgs: list[Message] = [
        UserMessage(content=[TextContent(text="hello")]),
        AssistantMessage(content=[TextContent(text="hi there")]),
    ]
    result = await summarize(
        provider=provider,
        messages_to_summarize=msgs,
        existing=None,
        max_summary_tokens=512,
    )
    assert isinstance(result, CompactionSummary)
    assert result.summary == "Compressed summary of the chat."
    assert provider.calls[0]["max_output_tokens"] == 512
    # cubepi messages carry no explicit id, so summarized_message_ids ends up empty.
    assert result.summarized_message_ids == []


@pytest.mark.asyncio
async def test_merges_with_existing_summary() -> None:
    provider = _FakeProvider("Merged summary.")
    existing = CompactionSummary(
        summary="Older context.",
        summarized_message_ids=[],
        last_summarized_message_id=None,
    )
    msgs: list[Message] = [UserMessage(content=[TextContent(text="newer")])]
    result = await summarize(provider=provider, messages_to_summarize=msgs, existing=existing)
    # System prompt was extended with EXISTING_SUMMARY_SUFFIX containing prior text
    assert "Older context." in provider.calls[0]["system"]
    assert result.summary == "Merged summary."
