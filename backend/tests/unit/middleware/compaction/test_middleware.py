"""Tests for CompactionMiddleware (mocked summarizer LLM)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from cubebox.middleware.compaction.middleware import CompactionMiddleware


def _make_state(
    msgs: list[Any], compaction: Any = None, until: int | None = None
) -> dict[str, Any]:
    s: dict[str, Any] = {"messages": msgs}
    if compaction is not None:
        s["compaction"] = compaction
    if until is not None:
        s["compaction_until_msg_index"] = until
    return s


@pytest.mark.asyncio
async def test_below_threshold_no_action():
    summary_llm = AsyncMock()
    mw = CompactionMiddleware(
        summary_llm=summary_llm,
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    state = _make_state([HumanMessage(content="hi"), AIMessage(content="hello")])
    result = await mw.abefore_model(state)
    assert result is None
    summary_llm.bind.assert_not_called()


@pytest.mark.asyncio
async def test_triggers_when_over_threshold(monkeypatch):
    from cubebox.agents.state import CompactionSummary
    from cubebox.middleware.compaction import middleware as mw_mod

    fake = CompactionSummary(
        summary="user asked about X; agent answered Y",
        summarized_message_ids=["m1", "m2"],
        last_summarized_message_id="m2",
    )
    summarize_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [
        HumanMessage(content="x" * 5000, id="m1"),
        AIMessage(content="y" * 5000, id="m2"),
        HumanMessage(content="follow up", id="m3"),
        AIMessage(content="ok", id="m4"),
    ]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
        min_compact_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))

    assert result is not None
    assert result["compaction"] is fake
    assert result["compaction_until_msg_index"] == 2


@pytest.mark.asyncio
async def test_does_not_recompact_when_compressed_view_fits(monkeypatch):
    """Stable convo with existing summary whose compressed view fits the
    threshold must NOT trigger another summarize call, even if raw
    state.messages keeps growing.
    """
    from cubebox.agents.state import CompactionSummary
    from cubebox.middleware.compaction import middleware as mw_mod

    summarize_mock = AsyncMock()
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [HumanMessage(content=f"m{i}", id=f"m{i}") for i in range(46)] + [
        HumanMessage(content="recent1", id="r1"),
        AIMessage(content="recent2", id="r2"),
        HumanMessage(content="recent3", id="r3"),
        AIMessage(content="recent4", id="r4"),
    ]
    existing = CompactionSummary(
        summary="short summary",
        summarized_message_ids=[f"m{i}" for i in range(46)],
        last_summarized_message_id="m45",
    )
    state = _make_state(msgs, compaction=existing, until=46)

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(state)

    assert result is None, "must not re-summarize when compressed view fits threshold"
    summarize_mock.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_safe_boundary(monkeypatch):
    from cubebox.middleware.compaction import middleware as mw_mod

    summarize_mock = AsyncMock()
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [
        AIMessage(content="x" * 9000, id="m1"),
        AIMessage(content="y" * 9000, id="m2"),
    ]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))
    assert result is None
    summarize_mock.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_summarizer_fails(monkeypatch):
    from cubebox.middleware.compaction import middleware as mw_mod

    summarize_mock = AsyncMock(side_effect=RuntimeError("llm down"))
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [
        HumanMessage(content="x" * 5000, id="m1"),
        AIMessage(content="y" * 5000, id="m2"),
        HumanMessage(content="m3", id="m3"),
        AIMessage(content="m4", id="m4"),
    ]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
        min_compact_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))
    assert result is None  # graceful fallback


@pytest.mark.asyncio
async def test_awrap_projects_compressed_view():
    from cubebox.agents.state import CompactionSummary

    captured: dict[str, Any] = {}

    async def handler(req: Any) -> AIMessage:
        captured["messages"] = list(req.messages)
        return AIMessage(content="response")

    msgs = [
        HumanMessage(content="m1", id="m1"),
        AIMessage(content="m2", id="m2"),
        HumanMessage(content="m3", id="m3"),
        AIMessage(content="m4", id="m4"),
    ]
    summary = CompactionSummary(
        summary="prior context covered",
        summarized_message_ids=["m1", "m2"],
        last_summarized_message_id="m2",
    )

    class FakeRequest:
        def __init__(self) -> None:
            self.messages = list(msgs)
            self.state = {
                "messages": msgs,
                "compaction": summary,
                "compaction_until_msg_index": 2,
            }

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    req = FakeRequest()
    await mw.awrap_model_call(req, handler)  # type: ignore[arg-type]

    sent = captured["messages"]
    assert isinstance(sent[0], SystemMessage)
    assert "prior context covered" in sent[0].content
    assert [m.id for m in sent[1:]] == ["m3", "m4"]


@pytest.mark.asyncio
async def test_awrap_passes_through_when_no_compaction():
    captured: dict[str, Any] = {}

    async def handler(req: Any) -> AIMessage:
        captured["messages"] = list(req.messages)
        return AIMessage(content="ok")

    msgs = [HumanMessage(content="hi", id="m1"), AIMessage(content="hello", id="m2")]

    class FakeRequest:
        def __init__(self) -> None:
            self.messages = list(msgs)
            self.state = {"messages": msgs}

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    await mw.awrap_model_call(FakeRequest(), handler)  # type: ignore[arg-type]
    assert [m.id for m in captured["messages"]] == ["m1", "m2"]
