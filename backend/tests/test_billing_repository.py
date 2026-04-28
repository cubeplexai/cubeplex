"""Unit tests for BillingRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.models.billing import BillingEvent, LlmBillingEvent
from cubebox.repositories.billing import BillingRepository


@pytest.fixture()
async def session():
    """In-memory SQLite session for fast unit tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


def _make_billing_event(**kwargs) -> BillingEvent:
    defaults = {
        "org_id": "org-1",
        "workspace_id": "ws-1",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "event_type": "llm_call",
        "cost_amount_micro": 1500,
        "currency": "USD",
        "started_at": datetime.now(UTC),
        "ended_at": datetime.now(UTC),
        "duration_ms": 300,
        "status": "success",
    }
    return BillingEvent(**{**defaults, **kwargs})


def _make_llm_event(billing_event_id: str, **kwargs) -> LlmBillingEvent:
    defaults = {
        "billing_event_id": billing_event_id,
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "input_tokens": 100,
        "output_tokens": 50,
        "price_input_per_mtok_micro": 150_000,
        "price_output_per_mtok_micro": 600_000,
    }
    return LlmBillingEvent(**{**defaults, **kwargs})


async def test_insert_llm_event_persists_both_rows(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    be = _make_billing_event()
    le = _make_llm_event(be.id)

    await repo.insert_llm_event(be, le)

    from sqlalchemy import select

    result_be = await session.execute(select(BillingEvent).where(BillingEvent.id == be.id))
    result_le = await session.execute(
        select(LlmBillingEvent).where(LlmBillingEvent.billing_event_id == be.id)
    )
    assert result_be.scalar_one_or_none() is not None
    assert result_le.scalar_one_or_none() is not None


async def test_record_fallback_failure_writes_error_row(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    now = datetime.now(UTC)

    await repo.record_fallback_failure(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        conversation_id="conv-1",
        provider="openai",
        model_id="gpt-4o",
        started_at=now,
        ended_at=now,
        error_class="RateLimitError",
    )

    from sqlalchemy import select

    result = await session.execute(
        select(BillingEvent).where(BillingEvent.status == "fallback_failed")
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.cost_amount_micro == 0
