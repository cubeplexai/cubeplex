"""CompactionMiddleware unit tests (M3.b.2).

Covers:
- No compaction when compressed view is under the token threshold.
- Compressed view is built correctly from existing summary + boundary.
- New summary is generated and written to extra when over threshold.
- Summarizer failure → falls back to current compressed view (no crash).
- approx_tokens: basic token estimation.
- _compressed_view: boundary=0 / None → passthrough.
"""

from __future__ import annotations

from typing import Any

import pytest
from cubepi.providers.base import AssistantMessage, Message, TextContent, UserMessage

from cubebox.middleware.compaction import (
    CompactionMiddleware,
    _compressed_view,
)
from cubebox.middleware.compaction.summarizer import CompactionSummary
from cubebox.middleware.compaction.tokens import approx_tokens

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason="stop",
    )


def _make_extra(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


class _FakeOneShotProvider:
    """Test double satisfying the summarizer's ``_OneShotProvider`` Protocol."""

    def __init__(
        self,
        *,
        reply: str = "summary text",
        raises: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.raises = raises
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
        if self.raises is not None:
            raise self.raises
        return self.reply


def _make_middleware(
    extra: dict[str, Any],
    *,
    max_tokens_before: int = 1000,
    summarizer_result: CompactionSummary | None = None,
    summarizer_raises: Exception | None = None,
) -> CompactionMiddleware:
    """Build a CompactionMiddleware with a fake one-shot summary LLM."""
    reply = summarizer_result.summary if summarizer_result else "summary text"
    fake_llm = _FakeOneShotProvider(reply=reply, raises=summarizer_raises)

    return CompactionMiddleware(
        extra_ref=lambda: extra,
        summary_llm=fake_llm,
        max_tokens_before_compact=max_tokens_before,
        keep_recent_messages=2,
        max_summary_tokens=512,
        min_compact_messages=2,
    )


# ---------------------------------------------------------------------------
# approx_tokens
# ---------------------------------------------------------------------------


def test_approx_tokens_empty() -> None:
    assert approx_tokens([]) == 0


def test_approx_tokens_user_message() -> None:
    msgs: list[Message] = [_user("hello")]
    tokens = approx_tokens(msgs)
    # "hello" = 5 chars, / 2.0 chars_per_token = 2 tokens
    assert tokens == 2


def test_approx_tokens_multiple_messages() -> None:
    msgs: list[Message] = [_user("a" * 100), _assistant("b" * 100)]
    tokens = approx_tokens(msgs)
    # 200 chars / 2.0 = 100 tokens
    assert tokens == 100


# ---------------------------------------------------------------------------
# _compressed_view
# ---------------------------------------------------------------------------


def test_compressed_view_no_summary_returns_original() -> None:
    msgs: list[Message] = [_user("hi"), _assistant("hello")]
    result = _compressed_view(msgs, None, None)
    assert result == msgs


def test_compressed_view_boundary_zero_returns_original() -> None:
    msgs: list[Message] = [_user("hi"), _assistant("hello")]
    summary = CompactionSummary(summary="prev summary")
    result = _compressed_view(msgs, summary, 0)
    assert result == msgs


def test_compressed_view_with_summary_and_boundary() -> None:
    msgs: list[Message] = [
        _user("turn 1"),
        _assistant("reply 1"),
        _user("turn 2"),
        _assistant("reply 2"),
    ]
    summary = CompactionSummary(summary="old turns summary")
    result = _compressed_view(msgs, summary, 2)
    assert len(result) == 3  # summary_msg + 2 recent
    assert isinstance(result[0], UserMessage)
    assert "old turns summary" in result[0].content[0].text  # type: ignore[union-attr]
    assert result[1] == msgs[2]
    assert result[2] == msgs[3]


# ---------------------------------------------------------------------------
# transform_context: under threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_compaction_when_under_threshold() -> None:
    """Below threshold → returns compressed view unchanged (no summarizer call)."""
    msgs: list[Message] = [_user("hi"), _assistant("hello")]
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra, max_tokens_before=100_000)

    result = await mw.transform_context(msgs)

    # No summary in extra → passthrough
    assert result == msgs
    assert "compaction" not in extra


@pytest.mark.asyncio
async def test_compressed_view_returned_when_summary_exists_and_under_threshold() -> None:
    """Existing summary + boundary → compressed view; no new summarizer call."""
    msgs: list[Message] = [
        _user("old1"),
        _assistant("old2"),
        _user("recent1"),
        _assistant("recent2"),
    ]
    summary = CompactionSummary(summary="Earlier turns compressed")
    extra: dict[str, Any] = {
        "compaction": summary,
        "compaction_until_msg_index": 2,
    }
    mw = _make_middleware(extra, max_tokens_before=100_000)

    result = await mw.transform_context(msgs)

    # Should be compressed view: [summary_msg, msgs[2], msgs[3]]
    assert len(result) == 3
    assert isinstance(result[0], UserMessage)
    assert "Earlier turns compressed" in result[0].content[0].text  # type: ignore[union-attr]
    assert result[1] == msgs[2]
    assert result[2] == msgs[3]
    # extra unchanged
    assert extra["compaction"] is summary
    assert extra["compaction_until_msg_index"] == 2


# ---------------------------------------------------------------------------
# transform_context: over threshold → writes new summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_new_summary_when_over_threshold() -> None:
    """When over threshold, new summary is generated and written to extra."""
    # Build 6 messages: 4 old + 2 recent — enough for boundary detection
    msgs: list[Message] = [
        _user("turn 1"),
        _assistant("reply 1"),
        _user("turn 2"),
        _assistant("reply 2"),
        _user("turn 3"),
        _assistant("reply 3"),
    ]
    extra: dict[str, Any] = {}

    # Set threshold to 1 token to force compaction
    mw = _make_middleware(extra, max_tokens_before=1)

    result = await mw.transform_context(msgs)

    # After compaction: extra should have a summary and boundary
    assert "compaction" in extra
    assert isinstance(extra["compaction"], CompactionSummary)
    assert isinstance(extra["compaction_until_msg_index"], int)
    boundary = extra["compaction_until_msg_index"]
    assert boundary > 0

    # Result should be the compressed view
    assert isinstance(result[0], UserMessage)
    assert len(result) == len(msgs) - boundary + 1  # summary_msg + msgs[boundary:]


@pytest.mark.asyncio
async def test_updates_existing_summary_when_over_threshold() -> None:
    """When already-summarized state is over threshold, new boundary advances."""
    msgs: list[Message] = [
        _user("t1"),
        _assistant("r1"),
        _user("t2"),
        _assistant("r2"),
        _user("t3"),
        _assistant("r3"),
        _user("t4"),
        _assistant("r4"),
    ]
    prior_summary = CompactionSummary(
        summary="first two turns",
        summarized_message_ids=["id1", "id2"],
    )
    extra: dict[str, Any] = {
        "compaction": prior_summary,
        "compaction_until_msg_index": 2,
    }

    # Force compaction
    mw = _make_middleware(extra, max_tokens_before=1)

    result = await mw.transform_context(msgs)

    new_boundary = extra["compaction_until_msg_index"]
    assert new_boundary > 2  # boundary must have advanced
    new_summary = extra["compaction"]
    assert isinstance(new_summary, CompactionSummary)
    # Result is compressed view with new summary
    assert isinstance(result[0], UserMessage)


# ---------------------------------------------------------------------------
# Summarizer failure → graceful fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarizer_failure_returns_compressed_view() -> None:
    """When summarizer raises, falls back to current compressed view."""
    msgs: list[Message] = [
        _user("t1"),
        _assistant("r1"),
        _user("t2"),
        _assistant("r2"),
        _user("t3"),
        _assistant("r3"),
    ]
    extra: dict[str, Any] = {}

    mw = _make_middleware(
        extra,
        max_tokens_before=1,
        summarizer_raises=RuntimeError("LLM unavailable"),
    )

    # Should NOT raise
    result = await mw.transform_context(msgs)

    # extra should NOT have been written on failure
    assert "compaction" not in extra

    # Result is the (unmodified) passthrough since no existing summary
    assert result == msgs
