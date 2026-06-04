"""Unit tests for CitationMiddleware (M3.a.3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from cubepi.agent.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentToolResult,
)
from cubepi.providers.base import AssistantMessage, TextContent, ToolCall, Usage

from cubebox.middleware.citation import CitationMiddleware, _extract_text_content
from cubebox.middleware.citations.config import CitationConfig
from cubebox.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_WEB_CONFIG = CitationConfig(
    source_type="web",
    content_field="results",
    mapping={"url": "url", "title": "title", "snippet": "snippet"},
)

_PLAIN_CONFIG = CitationConfig(
    source_type="web",
    content_field=None,  # raw text
    mapping={"snippet": "text"},
)


def _make_middleware(
    configs: dict[str, CitationConfig] | None = None,
    event_queue: asyncio.Queue[Any] | None = None,
) -> CitationMiddleware:
    return CitationMiddleware(
        citation_configs=configs or {"web_search": _WEB_CONFIG},
        event_queue=event_queue,
    )


def _make_tool_call(name: str = "web_search", args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(id="tc-1", name=name, arguments=args or {})


def _make_result(content_text: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=content_text)])


def _make_context(tool_call: ToolCall, result: AgentToolResult) -> AfterToolCallContext:
    assistant = AssistantMessage(content=[tool_call], usage=Usage())
    agent_ctx = AgentContext(system_prompt="", messages=[])
    return AfterToolCallContext(
        assistant_message=assistant,
        tool_call=tool_call,
        args={},
        result=result,
        is_error=False,
        context=agent_ctx,
    )


def _set_counter(start: int = 1) -> CitationCounter:
    counter = CitationCounter(start=start)
    citation_counter_var.set(counter)
    return counter


# ---------------------------------------------------------------------------
# _extract_text_content helper
# ---------------------------------------------------------------------------


def test_extract_text_content_from_text_content_object() -> None:
    block = TextContent(text="hello world")
    assert _extract_text_content([block]) == "hello world"


def test_extract_text_content_from_dict_block() -> None:
    block = {"type": "text", "text": "from dict"}
    assert _extract_text_content([block]) == "from dict"


def test_extract_text_content_joins_multiple() -> None:
    blocks = [TextContent(text="line1"), TextContent(text="line2")]
    assert _extract_text_content(blocks) == "line1\nline2"


def test_extract_text_content_empty_list() -> None:
    assert _extract_text_content([]) == ""


# ---------------------------------------------------------------------------
# No matching config → pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_none() -> None:
    mw = _make_middleware(configs={"web_search": _WEB_CONFIG})
    tool_call = _make_tool_call(name="calculator")
    result = _make_result('{"value": 42}')
    ctx = _make_context(tool_call, result)
    _set_counter()
    out = await mw.after_tool_call(ctx)
    assert out is None


@pytest.mark.asyncio
async def test_empty_configs_returns_none() -> None:
    mw = _make_middleware(configs={})
    tool_call = _make_tool_call(name="web_search")
    result = _make_result('{"results": []}')
    ctx = _make_context(tool_call, result)
    _set_counter()
    out = await mw.after_tool_call(ctx)
    assert out is None


# ---------------------------------------------------------------------------
# No CitationCounter in context → warning + None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_counter_in_context_returns_none() -> None:
    # Ensure no counter is set
    citation_counter_var.set(None)
    mw = _make_middleware()
    tool_call = _make_tool_call()
    payload = json.dumps({"results": [{"url": "http://x.com", "title": "X", "snippet": "abc"}]})
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)
    out = await mw.after_tool_call(ctx)
    assert out is None


# ---------------------------------------------------------------------------
# Happy path: JSON array result with items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_result_extracts_citations() -> None:
    _set_counter(start=1)
    mw = _make_middleware()

    snippet = "x" * 250  # long enough to survive chunking
    payload = json.dumps(
        {
            "results": [
                {"url": "http://example.com", "title": "Example", "snippet": snippet},
            ]
        }
    )
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)

    assert isinstance(out, AfterToolCallResult)
    assert out.details is not None
    citations = out.details["citations"]
    assert len(citations) == 1
    c = citations[0]
    assert c["citation_id"] == 1
    assert c["tool_call_id"] == "tc-1"
    assert c["metadata"]["source_type"] == "web"
    assert c["metadata"]["url"] == "http://example.com"
    assert c["metadata"]["title"] == "Example"
    assert len(c["chunks"]) >= 1


@pytest.mark.asyncio
async def test_multiple_items_produce_multiple_citations() -> None:
    _set_counter(start=1)
    mw = _make_middleware()

    snippet = "y" * 250
    payload = json.dumps(
        {
            "results": [
                {"url": "http://a.com", "title": "A", "snippet": snippet},
                {"url": "http://b.com", "title": "B", "snippet": snippet},
            ]
        }
    )
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    citations = out.details["citations"]
    assert len(citations) == 2
    assert citations[0]["citation_id"] == 1
    assert citations[1]["citation_id"] == 2
    assert citations[0]["metadata"]["url"] == "http://a.com"
    assert citations[1]["metadata"]["url"] == "http://b.com"


@pytest.mark.asyncio
async def test_citation_ids_increment_across_calls() -> None:
    """Counter must be shared; second call gets IDs starting from 3."""
    _set_counter(start=1)
    mw = _make_middleware()

    snippet = "z" * 250
    payload = json.dumps(
        {
            "results": [
                {"url": "http://c.com", "title": "C", "snippet": snippet},
                {"url": "http://d.com", "title": "D", "snippet": snippet},
            ]
        }
    )

    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out1 = await mw.after_tool_call(ctx)
    assert out1 is not None
    ids1 = [c["citation_id"] for c in out1.details["citations"]]
    assert ids1 == [1, 2]

    out2 = await mw.after_tool_call(ctx)
    assert out2 is not None
    ids2 = [c["citation_id"] for c in out2.details["citations"]]
    assert ids2 == [3, 4]


# ---------------------------------------------------------------------------
# Plain-text (non-JSON) result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_raw_text_treated_as_single_item() -> None:
    """When content_field is None and the result is not JSON, treat raw text as one item."""
    _set_counter()
    mw = _make_middleware(configs={"web_fetch": _PLAIN_CONFIG})

    raw_text = "This is a long body of text. " * 20  # > 200 chars
    tool_call = _make_tool_call(name="web_fetch")
    result = _make_result(raw_text)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    citations = out.details["citations"]
    assert len(citations) == 1
    assert citations[0]["citation_id"] == 1
    assert len(citations[0]["chunks"]) >= 1


# ---------------------------------------------------------------------------
# Empty / no chunks → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_snippet_returns_none() -> None:
    """If no chunks are produced (empty text), return None."""
    _set_counter()
    mw = _make_middleware()

    payload = json.dumps(
        {
            "results": [
                {"url": "http://x.com", "title": "X", "snippet": ""},
            ]
        }
    )
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is None


@pytest.mark.asyncio
async def test_empty_results_array_returns_none() -> None:
    _set_counter()
    mw = _make_middleware()
    payload = json.dumps({"results": []})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is None


# ---------------------------------------------------------------------------
# Event queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citations_pushed_to_provided_event_queue() -> None:
    _set_counter()
    q: asyncio.Queue[Any] = asyncio.Queue()
    mw = _make_middleware(event_queue=q)

    snippet = "w" * 250
    payload = json.dumps({"results": [{"url": "http://z.com", "title": "Z", "snippet": snippet}]})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    await mw.after_tool_call(ctx)

    assert not q.empty()
    event = q.get_nowait()
    assert event[0] == "citation"
    assert event[2]["citation_id"] == 1


@pytest.mark.asyncio
async def test_citations_pushed_to_context_var_queue() -> None:
    """Falls back to citation_event_queue ContextVar when no direct queue provided."""
    _set_counter()
    q: asyncio.Queue[Any] = asyncio.Queue()
    citation_event_queue.set(q)

    try:
        mw = _make_middleware(event_queue=None)
        snippet = "v" * 250
        payload = json.dumps(
            {"results": [{"url": "http://q.com", "title": "Q", "snippet": snippet}]}
        )
        tool_call = _make_tool_call()
        result = _make_result(payload)
        ctx = _make_context(tool_call, result)

        await mw.after_tool_call(ctx)

        assert not q.empty()
        event = q.get_nowait()
        assert event[0] == "citation"
    finally:
        citation_event_queue.set(None)


# ---------------------------------------------------------------------------
# args_mapping fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_args_mapping_fills_missing_metadata() -> None:
    """When result item lacks a URL, args_mapping should fill it from tool args."""
    _set_counter()
    config = CitationConfig(
        source_type="web",
        content_field=None,
        mapping={"snippet": "text"},
        args_mapping={"url": "url"},
    )
    mw = _make_middleware(configs={"web_fetch": config})

    raw_text = "Fetched page content. " * 15
    tool_call = _make_tool_call(name="web_fetch", args={"url": "http://fetched.com"})
    result = _make_result(raw_text)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    citations = out.details["citations"]
    assert citations[0]["metadata"]["url"] == "http://fetched.com"


# ---------------------------------------------------------------------------
# Return type shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_rewrites_content_with_markers() -> None:
    """AfterToolCallResult must rewrite content so the LLM sees 【N-M】 markers."""
    _set_counter(start=1)
    mw = _make_middleware()

    snippet = "a" * 250
    payload = json.dumps({"results": [{"url": "http://r.com", "title": "R", "snippet": snippet}]})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    assert out.content is not None
    rewritten = _extract_text_content(out.content)
    # First chunk carries the metadata header.
    assert "【1-0】" in rewritten
    assert "url: http://r.com" in rewritten
    assert "title: R" in rewritten
    # Original raw JSON payload must NOT leak through.
    assert '"results"' not in rewritten
    # Every recorded chunk must appear in the rewritten content.
    for i, chunk in enumerate(out.details["citations"][0]["chunks"]):
        marker = f"【1-{i}】"
        assert marker in rewritten
        assert chunk["content"] in rewritten


@pytest.mark.asyncio
async def test_result_stashes_original_content_for_sse() -> None:
    """details["original_content"] must carry the pre-rewrite raw output so the
    SSE path can show the frontend a parseable preview (web_search etc.)."""
    _set_counter(start=1)
    mw = _make_middleware()

    snippet = "a" * 250
    payload = json.dumps({"results": [{"url": "http://r.com", "title": "R", "snippet": snippet}]})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    assert out.details["original_content"] == payload


@pytest.mark.asyncio
async def test_result_details_contain_citations_key() -> None:
    _set_counter()
    mw = _make_middleware()

    snippet = "b" * 250
    payload = json.dumps({"results": [{"url": "http://s.com", "title": "S", "snippet": snippet}]})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    assert "citations" in out.details
    assert isinstance(out.details["citations"], list)


# ---------------------------------------------------------------------------
# CitationConfig.content_type round-trip tests
# ---------------------------------------------------------------------------


def test_citation_config_content_type_defaults_to_json() -> None:
    cfg = CitationConfig(source_type="web", content_field="results", mapping={"snippet": "snippet"})
    assert cfg.content_type == "json"


def test_citation_config_content_type_text_round_trip() -> None:
    raw = {
        "content_type": "text",
        "source_type": "web",
        "content_field": None,
        "mapping": {"snippet": "text"},
    }
    cfg = CitationConfig(**raw)
    assert cfg.content_type == "text"

    # Round-trip through serialize → deserialize.
    dumped = cfg.model_dump()
    assert dumped["content_type"] == "text"
    restored = CitationConfig.model_validate(dumped)
    assert restored == cfg


def test_citation_config_rejects_unknown_content_type() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CitationConfig(content_type="binary", source_type="web", content_field=None, mapping={})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# content_type="text" takes the fast path (no JSON parse)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# transform_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_when_configs_present() -> None:
    from cubebox.prompts.citations import CITATION_PROMPT

    mw = _make_middleware()  # default: web_search config
    out = await mw.transform_system_prompt("BASE", ctx=object())
    assert out.startswith("BASE")
    assert CITATION_PROMPT in out


@pytest.mark.asyncio
async def test_transform_system_prompt_passthrough_when_no_configs() -> None:
    # Bypass _make_middleware's `configs or {...}` default for the empty case.
    mw = CitationMiddleware(citation_configs={})
    out = await mw.transform_system_prompt("BASE", ctx=object())
    assert out == "BASE"


# ---------------------------------------------------------------------------
# Meta header formatting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marker_header_excludes_source_type_and_chains_chunks() -> None:
    """source_type stays out of [meta_header]; only chunk 0 carries it."""
    _set_counter(start=5)
    mw = _make_middleware()

    # Build a snippet long enough to force >1 chunk.
    sentences = "Sentence one. " * 40
    payload = json.dumps({"results": [{"url": "http://a.com", "title": "A", "snippet": sentences}]})
    tool_call = _make_tool_call()
    result = _make_result(payload)
    ctx = _make_context(tool_call, result)

    out = await mw.after_tool_call(ctx)
    assert out is not None
    text = _extract_text_content(out.content or [])
    assert "source_type" not in text
    assert "【5-0】 [url: http://a.com | title: A]" in text
    # At least chunk 1 exists without the header repeated.
    assert "【5-1】" in text
    assert text.count("[url: http://a.com") == 1


# ---------------------------------------------------------------------------
# CitationCounter.seed_from_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_from_messages_advances_past_existing_markers() -> None:
    from cubepi.providers.base import ToolResultMessage

    counter = CitationCounter(start=1)
    msgs = [
        ToolResultMessage(
            tool_call_id="tc-old-1",
            tool_name="web_search",
            content=[TextContent(text="【3-0】 [url: http://a] alpha\n\n【3-1】 beta")],
        ),
        ToolResultMessage(
            tool_call_id="tc-old-2",
            tool_name="web_fetch",
            content=[TextContent(text="【7-0】 [url: http://b] gamma")],
        ),
    ]
    await counter.seed_from_messages(msgs)
    assert await counter.next() == 8


@pytest.mark.asyncio
async def test_seed_from_messages_noop_when_no_markers() -> None:
    from cubepi.providers.base import ToolResultMessage

    counter = CitationCounter(start=1)
    await counter.seed_from_messages(
        [
            ToolResultMessage(
                tool_call_id="tc-1",
                tool_name="web_search",
                content=[TextContent(text="plain result, no markers")],
            )
        ]
    )
    assert await counter.next() == 1


@pytest.mark.asyncio
async def test_seed_from_messages_does_not_regress_counter() -> None:
    """If counter is already ahead of historical max, leave it alone."""
    from cubepi.providers.base import ToolResultMessage

    counter = CitationCounter(start=20)
    await counter.seed_from_messages(
        [
            ToolResultMessage(
                tool_call_id="tc-1",
                tool_name="web_search",
                content=[TextContent(text="【2-0】 stale")],
            )
        ]
    )
    assert await counter.next() == 20


@pytest.mark.asyncio
async def test_seed_from_messages_ignores_non_tool_result_messages() -> None:
    from cubepi.providers.base import AssistantMessage, Usage

    counter = CitationCounter(start=1)
    # Assistant message with 【N-M】 in its own text must NOT advance the
    # counter — those are the LLM's outputs, not source markers.
    msg = AssistantMessage(content=[TextContent(text="cite 【9-0】 here")], usage=Usage())
    await counter.seed_from_messages([msg])
    assert await counter.next() == 1


@pytest.mark.asyncio
async def test_citation_middleware_uses_text_path_when_content_type_is_text() -> None:
    """content_type='text' skips JSON parse and treats raw output as one item."""
    cfg = CitationConfig(
        content_type="text",
        source_type="web",
        content_field=None,
        mapping={"snippet": "text"},
        args_mapping={"url": "url"},
    )
    mw = _make_middleware(configs={"web_fetch": cfg})
    _set_counter(start=1)

    q: asyncio.Queue[Any] = asyncio.Queue()
    citation_event_queue.set(q)
    try:
        tool_call = _make_tool_call(name="web_fetch", args={"url": "https://example.com/x"})
        result = _make_result("plain text content from the fetched URL " * 6)
        ctx = _make_context(tool_call, result)

        out = await mw.after_tool_call(ctx)

        assert out is not None
        citations = out.details["citations"]
        assert len(citations) == 1
        assert citations[0]["metadata"]["url"] == "https://example.com/x"
        assert any("plain text content" in chunk["content"] for chunk in citations[0]["chunks"])

        # Citation was also pushed to the event queue
        assert not q.empty()
        kind, _agent_id, payload = q.get_nowait()
        assert kind == "citation"
        assert payload["metadata"]["url"] == "https://example.com/x"
    finally:
        citation_event_queue.set(None)
