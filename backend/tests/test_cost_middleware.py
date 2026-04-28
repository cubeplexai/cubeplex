"""Unit tests for CostMiddleware."""

import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from cubebox.middleware.cost import CostMiddleware


def _make_ai_message(input_tokens: int = 100, output_tokens: int = 50) -> AIMessage:
    msg = AIMessage(content="hello")
    msg.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_token_details": {"cache_read": 10},
        "output_token_details": {"cache_write": 5},
    }
    return msg


def _make_model_cost(input: float = 0.15, output: float = 0.60) -> MagicMock:
    cost = MagicMock()
    cost.input = input
    cost.output = output
    cost.cache_read = 0.0
    cost.cache_write = 0.0
    cost.currency = "USD"
    return cost


def _make_llm(provider: str = "openai", model_id: str = "gpt-4o-mini") -> MagicMock:
    llm = MagicMock()
    llm._cubebox_provider = provider
    llm._cubebox_model_id = model_id
    llm._cubebox_model_cost = _make_model_cost()
    return llm


async def test_success_path_writes_billing_row() -> None:
    written: list[tuple] = []

    class FakeRepo:
        org_id = "org-1"

        async def insert_llm_event(self, be, le):
            written.append((be, le))

    middleware = CostMiddleware(
        repo=FakeRepo(),
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        conversation_id="conv-1",
    )

    request = MagicMock()
    request.model = _make_llm()
    response = MagicMock()
    response.result = _make_ai_message(input_tokens=100, output_tokens=50)

    async def handler(req):
        return response

    result = await middleware.awrap_model_call(request, handler)
    await asyncio.sleep(0.05)  # let fire-and-forget task complete

    assert result is response
    assert len(written) == 1
    be, le = written[0]
    assert be.status == "success"
    assert le.input_tokens == 100
    assert le.output_tokens == 50
    assert le.provider == "openai"
    assert le.model_id == "gpt-4o-mini"
    assert be.cost_amount_micro > 0


async def test_error_path_writes_error_row_and_reraises() -> None:
    written: list[tuple] = []

    class FakeRepo:
        org_id = "org-1"

        async def insert_llm_event(self, be, le):
            written.append((be, le))

    middleware = CostMiddleware(
        repo=FakeRepo(),
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        conversation_id="conv-1",
    )
    request = MagicMock()
    request.model = _make_llm()

    async def handler(req):
        raise ValueError("LLM failed")

    with pytest.raises(ValueError, match="LLM failed"):
        await middleware.awrap_model_call(request, handler)

    await asyncio.sleep(0.05)
    assert len(written) == 1
    be, le = written[0]
    assert be.status == "error"
    assert le.error_class == "ValueError"
    assert le.input_tokens == 0


async def test_cost_calculation_uses_snapshot_price() -> None:
    written: list[tuple] = []

    class FakeRepo:
        org_id = "org-1"

        async def insert_llm_event(self, be, le):
            written.append((be, le))

    middleware = CostMiddleware(
        repo=FakeRepo(),
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        conversation_id="conv-1",
    )
    llm = _make_llm()
    llm._cubebox_model_cost = _make_model_cost(input=0.15, output=0.60)

    request = MagicMock()
    request.model = llm
    response = MagicMock()
    response.result = _make_ai_message(input_tokens=1_000_000, output_tokens=0)

    async def handler(req):
        return response

    await middleware.awrap_model_call(request, handler)
    await asyncio.sleep(0.05)

    _be, le = written[0]
    # 1M input tokens × $0.15/1M = $0.15 = 150_000 micro
    assert le.price_input_per_mtok_micro == int(0.15 * 1_000_000)
