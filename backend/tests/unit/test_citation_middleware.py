# tests/unit/test_citation_middleware.py
import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from cubebox.middleware.citations.config import CitationConfig
from cubebox.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)
from cubebox.middleware.citations.middleware import CitationMiddleware


def _make_tool_call_request(
    tool_name: str,
    tool_call_id: str = "call_123",
) -> ToolCallRequest:
    """Build a minimal ToolCallRequest for testing."""
    runtime = SimpleNamespace(
        state={},
        context=None,
        config={"configurable": {}},
        stream_writer=None,
        tool_call_id=tool_call_id,
        store=None,
    )
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": {}, "id": tool_call_id},
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )


@pytest.fixture()
def web_search_config() -> dict[str, CitationConfig]:
    return {
        "web_search": CitationConfig(
            source_type="web",
            content_field="results",
            mapping={
                "url": "link",
                "title": "title",
                "snippet": "snippet",
            },
        ),
    }


@pytest.fixture()
def _setup_counter_and_queue():
    """Set up CitationCounter and event queue ContextVars for testing."""
    counter = CitationCounter(start=1)
    queue: asyncio.Queue[Any] = asyncio.Queue()
    ct = citation_counter_var.set(counter)
    qt = citation_event_queue.set(queue)
    yield queue
    citation_counter_var.reset(ct)
    citation_event_queue.reset(qt)


class TestCitationMiddlewareToolCall:
    async def test_no_config_passes_through(self, _setup_counter_and_queue):
        mw = CitationMiddleware(citation_configs={})
        original = ToolMessage(content="raw output", tool_call_id="call_1", name="calculator")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("calculator")

        result = await mw.awrap_tool_call(request, handler)
        assert result.content == "raw output"

    async def test_unconfigured_tool_passes_through(
        self, web_search_config, _setup_counter_and_queue
    ):
        mw = CitationMiddleware(citation_configs=web_search_config)
        original = ToolMessage(content="42", tool_call_id="call_1", name="calculator")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("calculator")

        result = await mw.awrap_tool_call(request, handler)
        assert result.content == "42"

    async def test_configured_tool_rewrites_content(
        self, web_search_config, _setup_counter_and_queue
    ):
        tool_output = json.dumps(
            {
                "results": [
                    {"link": "https://a.com", "title": "A", "snippet": "Content about A."},
                ]
            }
        )
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        result = await mw.awrap_tool_call(request, handler)

        assert "【1-0】" in result.content
        assert "Content about A." in result.content

    async def test_original_content_preserved(self, web_search_config, _setup_counter_and_queue):
        tool_output = json.dumps(
            {
                "results": [
                    {"link": "https://a.com", "title": "A", "snippet": "Content."},
                ]
            }
        )
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        result = await mw.awrap_tool_call(request, handler)

        assert result.additional_kwargs["original_content"] == tool_output

    async def test_citation_event_pushed_to_queue(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        tool_output = json.dumps(
            {
                "results": [
                    {"link": "https://a.com", "title": "A", "snippet": "Some content."},
                ]
            }
        )
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search", tool_call_id="call_1")

        mw = CitationMiddleware(citation_configs=web_search_config)
        await mw.awrap_tool_call(request, handler)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item[0] == "citation"
        citation_data = item[2]
        assert citation_data["citation_id"] == 1
        assert citation_data["metadata"]["source_type"] == "web"
        assert citation_data["metadata"]["url"] == "https://a.com"
        assert citation_data["tool_call_id"] == "call_1"

    async def test_multiple_results_get_different_ids(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        tool_output = json.dumps(
            {
                "results": [
                    {"link": "https://a.com", "title": "A", "snippet": "Content A."},
                    {"link": "https://b.com", "title": "B", "snippet": "Content B."},
                ]
            }
        )
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        await mw.awrap_tool_call(request, handler)

        ids = []
        while not queue.empty():
            item = queue.get_nowait()
            ids.append(item[2]["citation_id"])
        assert ids == [1, 2]

    async def test_counter_continues_across_calls(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        mw = CitationMiddleware(citation_configs=web_search_config)
        for i in range(2):
            tool_output = json.dumps(
                {
                    "results": [
                        {"link": f"https://{i}.com", "title": f"T{i}", "snippet": f"Content {i}."},
                    ]
                }
            )
            original = ToolMessage(content=tool_output, tool_call_id=f"call_{i}", name="web_search")
            handler = AsyncMock(return_value=original)
            request = _make_tool_call_request("web_search", tool_call_id=f"call_{i}")
            await mw.awrap_tool_call(request, handler)

        ids = []
        while not queue.empty():
            item = queue.get_nowait()
            ids.append(item[2]["citation_id"])
        assert ids == [1, 2]


    async def test_web_fetch_plain_text_gets_url_from_args(self, _setup_counter_and_queue):
        """web_fetch returns plain text; URL should come from tool call args via args_mapping."""
        queue = _setup_counter_and_queue
        configs = {
            "web_fetch": CitationConfig(
                source_type="web",
                content_field=None,
                mapping={"snippet": "text"},
                args_mapping={"url": "url", "title": "title"},
            ),
        }
        mw = CitationMiddleware(citation_configs=configs)
        original = ToolMessage(
            content="This is the fetched page content about AI.",
            tool_call_id="call_fetch",
            name="web_fetch",
        )
        handler = AsyncMock(return_value=original)

        runtime = SimpleNamespace(
            state={},
            context=None,
            config={"configurable": {}},
            stream_writer=None,
            tool_call_id="call_fetch",
            store=None,
        )
        request = ToolCallRequest(
            tool_call={
                "name": "web_fetch",
                "args": {"url": "https://example.com/page", "title": "Example Page"},
                "id": "call_fetch",
            },
            tool=None,
            state={"messages": []},
            runtime=runtime,
        )

        result = await mw.awrap_tool_call(request, handler)

        assert "【1-0】" in result.content
        assert not queue.empty()
        item = queue.get_nowait()
        citation_data = item[2]
        assert citation_data["metadata"]["url"] == "https://example.com/page"
        assert citation_data["metadata"]["title"] == "Example Page"
        assert citation_data["metadata"]["source_type"] == "web"


class TestCitationMiddlewareModelCall:
    async def test_injects_prompt_when_configs_exist(self, web_search_config):
        mw = CitationMiddleware(citation_configs=web_search_config)
        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=[],
            system_message=SystemMessage(content="Base prompt"),
        )
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="hi")]))
        await mw.awrap_model_call(request, handler)

        called_request = handler.call_args[0][0]
        system_content = called_request.system_message.content
        assert "Citation Rules" in system_content
        assert "【N-M】" in system_content

    async def test_no_injection_when_no_configs(self):
        mw = CitationMiddleware(citation_configs={})
        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=[],
            system_message=SystemMessage(content="Base prompt"),
        )
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="hi")]))
        await mw.awrap_model_call(request, handler)

        called_request = handler.call_args[0][0]
        system_content = called_request.system_message.content
        assert "Citation Rules" not in system_content

    async def test_no_tools_property(self, web_search_config):
        mw = CitationMiddleware(citation_configs=web_search_config)
        assert len(mw.tools) == 0
