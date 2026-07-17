"""Unit tests for BillingRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.models.billing import BillingEvent, LlmBillingEvent
from cubeplex.repositories.billing import BillingRepository


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

    # Verify child LlmBillingEvent was also written
    result_le = await session.execute(
        select(LlmBillingEvent).where(LlmBillingEvent.billing_event_id == row.id)
    )
    le_row = result_le.scalar_one_or_none()
    assert le_row is not None
    assert le_row.error_class == "RateLimitError"
    assert le_row.input_tokens == 0
    assert le_row.output_tokens == 0


async def test_get_workspace_spend_sums_by_day(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)

    for _i in range(3):
        be = _make_billing_event(
            workspace_id="ws-1",
            cost_amount_micro=1_000_000,
            started_at=now,
            ended_at=now,
        )
        le = _make_llm_event(be.id, input_tokens=100, output_tokens=50)
        await repo.insert_llm_event(be, le)

    rows = await repo.get_workspace_spend(
        workspace_id="ws-1",
        since=datetime(2026, 4, 1, tzinfo=UTC),
        until=datetime(2026, 4, 30, tzinfo=UTC),
        group_by="day",
    )
    assert len(rows) == 1
    assert rows[0]["call_count"] == 3
    assert rows[0]["cost_amount_micro"] == 3_000_000
    assert rows[0]["input_tokens"] == 300


async def test_get_org_spend_groups_by_workspace(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)

    for ws in ("ws-a", "ws-b"):
        be = _make_billing_event(
            workspace_id=ws,
            cost_amount_micro=500_000,
            started_at=now,
            ended_at=now,
        )
        le = _make_llm_event(be.id)
        await repo.insert_llm_event(be, le)

    rows = await repo.get_org_spend(
        since=datetime(2026, 4, 1, tzinfo=UTC),
        until=datetime(2026, 4, 30, tzinfo=UTC),
        group_by="workspace",
    )
    assert len(rows) == 2
    workspaces = {r["bucket"] for r in rows}
    assert workspaces == {"ws-a", "ws-b"}
    for r in rows:
        assert r["cost_amount_micro"] == 500_000
        assert r["call_count"] == 1


async def test_stream_events_for_export_yields_all_rows(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)

    for _ in range(5):
        be = _make_billing_event(workspace_id="ws-1", started_at=now, ended_at=now)
        le = _make_llm_event(be.id)
        await repo.insert_llm_event(be, le)

    rows = []
    async for row in repo.stream_events_for_export(
        since=datetime(2026, 4, 1, tzinfo=UTC),
        until=datetime(2026, 4, 30, tzinfo=UTC),
    ):
        rows.append(row)

    assert len(rows) == 5
    assert "provider" in rows[0]
    assert "model_id" in rows[0]
