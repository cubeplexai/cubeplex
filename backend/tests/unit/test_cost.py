"""Unit tests for CostMiddleware (M3.d.1).

Covers:
- Constructor stores all fields correctly.
- after_model_response writes a billing record with correct attribution.
- after_model_response updates _last_billing_id after each call.
- after_model_response returns None (no response mutation, no decision).
- _subagent_depth is carried correctly on the instance.
- Usage fields (including cache tokens) are extracted and written.
- None usage falls back to zeros.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cubepi.providers.base import AssistantMessage, TextContent, Usage

from cubeplex.llm.config import ModelCost
from cubeplex.middleware.cost import CostMiddleware, _compute_cost_micro, _extract_usage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    provider_id: str = "anthropic",
    model_id: str = "claude-3-5-sonnet-20241022",
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="Hello")],
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
        provider_id=provider_id,
        model_id=model_id,
    )


def _make_response_no_usage(
    *,
    provider_id: str = "anthropic",
    model_id: str = "claude-3-5-sonnet",
) -> AssistantMessage:
    """AssistantMessage with usage=None (error path / provider gaps)."""
    return AssistantMessage(
        content=[TextContent(text="oops")],
        usage=None,
        provider_id=provider_id,
        model_id=model_id,
    )


def _make_middleware(**kwargs: Any) -> CostMiddleware:
    defaults: dict[str, Any] = {
        "org_id": "org-1",
        "workspace_id": "ws-1",
        "user_id": "usr-1",
        "conversation_id": "conv-1",
    }
    defaults.update(kwargs)
    return CostMiddleware(**defaults)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_stores_all_fields() -> None:
    mw = CostMiddleware(
        org_id="org-abc",
        workspace_id="ws-xyz",
        user_id="usr-42",
        conversation_id="conv-99",
        parent_billing_id="bill-parent",
        subagent_depth=2,
    )
    assert mw._org_id == "org-abc"
    assert mw._workspace_id == "ws-xyz"
    assert mw._user_id == "usr-42"
    assert mw._conversation_id == "conv-99"
    assert mw._parent_billing_id == "bill-parent"
    assert mw._subagent_depth == 2
    assert mw._last_billing_id is None


def test_constructor_defaults() -> None:
    mw = _make_middleware()
    assert mw._parent_billing_id is None
    assert mw._subagent_depth == 0
    assert mw._last_billing_id is None


def test_subagent_depth_carried() -> None:
    mw = _make_middleware(subagent_depth=3)
    assert mw._subagent_depth == 3


# ---------------------------------------------------------------------------
# after_model_response: return value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_model_response_returns_none() -> None:
    mw = _make_middleware()
    response = _make_response()
    ctx = MagicMock()

    with patch("cubeplex.middleware.cost.asyncio.create_task"):
        result = await mw.after_model_response(response, ctx)

    assert result is None


# ---------------------------------------------------------------------------
# after_model_response: _last_billing_id tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_model_response_updates_last_billing_id() -> None:
    """_last_billing_id is set after each call."""
    mw = _make_middleware()
    assert mw._last_billing_id is None

    response = _make_response()
    ctx = MagicMock()

    with patch("cubeplex.middleware.cost.asyncio.create_task"):
        await mw.after_model_response(response, ctx)

    assert mw._last_billing_id is not None
    assert mw._last_billing_id.startswith("bill")


@pytest.mark.asyncio
async def test_after_model_response_advances_billing_id_each_call() -> None:
    """A second call produces a different _last_billing_id."""
    mw = _make_middleware()
    ctx = MagicMock()
    response = _make_response()

    with patch("cubeplex.middleware.cost.asyncio.create_task"):
        await mw.after_model_response(response, ctx)
        first_id = mw._last_billing_id

        await mw.after_model_response(response, ctx)
        second_id = mw._last_billing_id

    assert first_id != second_id


# ---------------------------------------------------------------------------
# after_model_response: billing record attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_model_response_writes_billing_record_with_correct_attribution() -> None:
    """_write is called with correct org/workspace/user/conversation attribution."""
    mw = CostMiddleware(
        org_id="org-billing",
        workspace_id="ws-billing",
        user_id="usr-billing",
        conversation_id="conv-billing",
        parent_billing_id="bill-parent-42",
        subagent_depth=1,
    )
    response = _make_response(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=20,
        cache_write_tokens=10,
        provider_id="anthropic",
        model_id="claude-opus-4-5",
    )
    ctx = MagicMock()

    inserted_be: list[Any] = []
    inserted_le: list[Any] = []

    class _FakeRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            self.org_id = org_id

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            inserted_be.append(be)
            inserted_le.append(le)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _FakeRepo),
    ):
        await mw.after_model_response(response, ctx)
        # Allow the fire-and-forget task to complete
        await asyncio.sleep(0)

    assert len(inserted_be) == 1
    assert len(inserted_le) == 1

    be = inserted_be[0]
    le = inserted_le[0]

    # BillingEvent attribution
    assert be.org_id == "org-billing"
    assert be.workspace_id == "ws-billing"
    assert be.user_id == "usr-billing"
    assert be.conversation_id == "conv-billing"
    assert be.event_type == "llm_call"
    assert be.status == "success"

    # LlmBillingEvent fields
    assert le.provider == "anthropic"
    assert le.model_id == "claude-opus-4-5"
    assert le.input_tokens == 100
    assert le.output_tokens == 50
    assert le.cache_read_tokens == 20
    assert le.cache_write_tokens == 10
    assert le.parent_run_id == "bill-parent-42"
    assert le.subagent_depth == 1


@pytest.mark.asyncio
async def test_after_model_response_billing_event_id_matches_llm_event() -> None:
    """BillingEvent.id == LlmBillingEvent.billing_event_id."""
    mw = _make_middleware()
    response = _make_response()
    ctx = MagicMock()

    inserted_be: list[Any] = []
    inserted_le: list[Any] = []

    class _FakeRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            pass

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            inserted_be.append(be)
            inserted_le.append(le)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _FakeRepo),
    ):
        await mw.after_model_response(response, ctx)
        await asyncio.sleep(0)

    assert inserted_be[0].id == inserted_le[0].billing_event_id


@pytest.mark.asyncio
async def test_after_model_response_id_matches_last_billing_id() -> None:
    """The run_id assigned to billing rows equals _last_billing_id."""
    mw = _make_middleware()
    response = _make_response()
    ctx = MagicMock()

    inserted_be: list[Any] = []

    class _FakeRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            pass

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            inserted_be.append(be)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _FakeRepo),
    ):
        await mw.after_model_response(response, ctx)
        await asyncio.sleep(0)

    assert inserted_be[0].id == mw._last_billing_id


# ---------------------------------------------------------------------------
# _extract_usage helper
# ---------------------------------------------------------------------------


def test_extract_usage_all_fields() -> None:
    response = _make_response(
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=40,
        cache_write_tokens=20,
    )
    result = _extract_usage(response)
    assert result == {
        "input_tokens": 200,
        "output_tokens": 100,
        "cache_read_tokens": 40,
        "cache_write_tokens": 20,
    }


def test_extract_usage_none_falls_back_to_zeros() -> None:
    response = _make_response_no_usage()
    result = _extract_usage(response)
    assert result == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


# ---------------------------------------------------------------------------
# Price lookup / cost computation (PR #84 review)
# ---------------------------------------------------------------------------


def test_compute_cost_micro_with_lookup_matches_manual_math() -> None:
    """ModelCost.input is $/million tokens; tokens * cost == micro-dollars."""
    price = ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 200,
        "cache_write_tokens": 10,
    }
    expected = int(100 * 3.0 + 50 * 15.0 + 200 * 0.3 + 10 * 3.75)
    # Manual sanity check: 300 + 750 + 60 + 37 = 1147 micro-USD.
    assert expected == 1147

    got = _compute_cost_micro(
        usage=usage,
        provider="anthropic",
        model_id="claude-opus-4-5",
        price_lookup=lambda _p, _m: price,
    )
    assert got == expected


def test_compute_cost_micro_no_lookup_returns_zero() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert (
        _compute_cost_micro(
            usage=usage,
            provider="anthropic",
            model_id="claude-opus-4-5",
            price_lookup=None,
        )
        == 0
    )


def test_compute_cost_micro_unknown_model_returns_zero() -> None:
    usage = {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert (
        _compute_cost_micro(
            usage=usage,
            provider="anthropic",
            model_id="unknown-id",
            price_lookup=lambda _p, _m: None,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_after_model_response_writes_computed_cost() -> None:
    """With a price_lookup, BillingEvent.cost_amount_micro reflects token * price."""
    price = ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
    mw = CostMiddleware(
        org_id="org-cost",
        workspace_id="ws-cost",
        user_id="usr-cost",
        conversation_id="conv-cost",
        price_lookup=lambda _p, _m: price,
    )
    response = _make_response(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_write_tokens=10,
    )
    ctx = MagicMock()

    inserted_be: list[Any] = []

    class _FakeRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            pass

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            inserted_be.append(be)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _FakeRepo),
    ):
        await mw.after_model_response(response, ctx)
        await asyncio.sleep(0)

    assert inserted_be[0].cost_amount_micro == 1147


@pytest.mark.asyncio
async def test_after_model_response_without_lookup_still_writes_row_with_zero_cost() -> None:
    """Regression guard: no price_lookup → cost_amount_micro=0 but row still written."""
    mw = _make_middleware()  # no price_lookup
    response = _make_response(input_tokens=100, output_tokens=50)
    ctx = MagicMock()

    inserted_be: list[Any] = []

    class _FakeRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            pass

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            inserted_be.append(be)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _FakeRepo),
    ):
        await mw.after_model_response(response, ctx)
        await asyncio.sleep(0)

    assert len(inserted_be) == 1
    assert inserted_be[0].cost_amount_micro == 0


def test_extract_usage_zero_cache_tokens() -> None:
    response = _make_response(
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    result = _extract_usage(response)
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0


# ---------------------------------------------------------------------------
# Billing write error swallowed (no exception propagation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billing_write_failure_does_not_raise() -> None:
    """DB write failure is logged and swallowed; after_model_response still returns None."""
    mw = _make_middleware()
    response = _make_response()
    ctx = MagicMock()

    class _BrokenRepo:
        def __init__(self, session: Any, *, org_id: str) -> None:
            pass

        async def insert_llm_event(self, be: Any, le: Any) -> None:
            raise RuntimeError("DB exploded")

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubeplex.middleware.cost.async_session_maker", return_value=fake_session),
        patch("cubeplex.middleware.cost.BillingRepository", _BrokenRepo),
    ):
        result = await mw.after_model_response(response, ctx)
        await asyncio.sleep(0)

    assert result is None
