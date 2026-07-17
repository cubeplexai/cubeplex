"""E2E tests for billing — assert billing rows are written on real LLM calls."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.billing import BillingEvent, LlmBillingEvent
from tests.e2e.conftest import DEFAULT_WS_ID
from tests.e2e.helpers import await_until

pytestmark = [pytest.mark.e2e, pytest.mark.real_llm]

_DEFAULT_WS = DEFAULT_WS_ID


@asynccontextmanager
async def _db_session() -> AsyncIterator[AsyncSession]:
    """Create a direct AsyncSession to the test DB (NullPool, no connection sharing)."""
    _engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await _engine.dispose()


async def _count_billing_rows(session: AsyncSession, conversation_id: str) -> int:
    result = await session.execute(
        select(func.count()).where(
            BillingEvent.conversation_id == conversation_id,
            BillingEvent.event_type == "llm_call",
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_send_message_creates_billing_event(
    async_client: httpx.AsyncClient,
) -> None:
    """Sending a message triggers at least one billing_events row."""
    # Create a conversation
    resp = await async_client.post(
        f"/api/v1/ws/{_DEFAULT_WS}/conversations",
        params={"title": "billing test"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    # Send a short message and stream until done
    async with async_client.stream(
        "POST",
        f"/api/v1/ws/{_DEFAULT_WS}/conversations/{conv_id}/messages",
        json={"content": "Say exactly: hello"},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: ") and '"type":"done"' in line:
                break

    # Billing is a fire-and-forget DB write triggered on stream `done`. Poll
    # until the row lands instead of guessing at a sleep duration.
    async def _has_billing_row() -> int:
        async with _db_session() as s:
            return await _count_billing_rows(s, conv_id)

    await await_until(
        _has_billing_row,
        timeout=5.0,
        message=f"billing row never appeared for conversation {conv_id}",
    )

    async with _db_session() as session:
        # Verify child row exists and has non-zero tokens
        result = await session.execute(
            select(BillingEvent, LlmBillingEvent)
            .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
            .where(BillingEvent.conversation_id == conv_id)
        )
        rows = result.all()
        assert len(rows) >= 1
        _be, le = rows[0]
        assert le.input_tokens > 0, "input_tokens should be populated by stream_usage=True"
        assert le.output_tokens > 0, "output_tokens should be populated by stream_usage=True"
        assert le.provider != "unknown"
        assert le.model_id != "unknown"
        assert _be.cost_amount_micro >= 0


@pytest.mark.asyncio
async def test_cost_summary_endpoint_returns_data(
    async_client: httpx.AsyncClient,
) -> None:
    """After a message is sent, /admin/cost/summary returns non-zero total_calls."""
    resp = await async_client.post(
        f"/api/v1/ws/{_DEFAULT_WS}/conversations",
        params={"title": "cost summary test"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    async with async_client.stream(
        "POST",
        f"/api/v1/ws/{_DEFAULT_WS}/conversations/{conv_id}/messages",
        json={"content": "Say: ok"},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("data: ") and '"type":"done"' in line:
                break

    async def _summary_has_calls() -> int:
        s = await async_client.get("/api/v1/admin/cost/summary")
        assert s.status_code == 200
        return int(s.json().get("total_calls", 0))

    await await_until(
        _summary_has_calls,
        timeout=5.0,
        message="/admin/cost/summary stayed at total_calls=0 after stream done",
    )
