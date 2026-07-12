# Cost Tracking (M1-E1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every LLM call's token usage and cost into a SQL billing table, expose an admin dashboard tab + CSV export, with a stable read-side API for future EE budget enforcement.

**Architecture:** `CostMiddleware` wraps `awrap_model_call` and fire-and-forgets a two-INSERT transaction into `billing_events` + `billing_llm_events` (parent/child tables). A `BillingRepository` handles all reads and writes. Admin API routes under `/api/v1/admin/cost/` serve the M2 console "成本" tab and CSV exports.

**Tech Stack:** Python/SQLModel/SQLAlchemy async (backend), Next.js/shadcn/TypeScript (frontend), MySQL (DB), Alembic (migrations), Playwright (frontend E2E).

**Worktree:** `/home/chris/cubeplex/.worktrees/m1-e1-cost-tracking` — all shell commands run from there unless otherwise noted.

---

## File Map

**New files:**
- `backend/cubeplex/models/billing.py` — `BillingEvent`, `LlmBillingEvent` SQLModel tables
- `backend/cubeplex/repositories/billing.py` — `BillingRepository` (insert + aggregate queries)
- `backend/cubeplex/middleware/cost.py` — `CostMiddleware` + `_cost_helper.py` utilities
- `backend/cubeplex/api/schemas/billing.py` — Pydantic response schemas
- `backend/cubeplex/api/routes/v1/cost.py` — 4 admin cost endpoints
- `backend/tests/test_billing_repository.py` — unit tests (real test DB)
- `backend/tests/test_cost_middleware.py` — unit tests (mock LLM handler)
- `backend/tests/e2e/test_billing.py` — E2E test (real LLM call → assert billing row)
- `frontend/packages/core/src/types/billing.ts` — TS interfaces
- `frontend/packages/core/src/api/billing.ts` — API client functions
- `frontend/packages/web/app/admin/cost/page.tsx` — cost dashboard page
- `frontend/packages/web/__tests__/e2e/admin-cost.spec.ts` — Playwright E2E

**Modified files:**
- `backend/cubeplex/llm/config.py` — add `currency: str = "USD"` to `ModelCost`
- `backend/cubeplex/llm/factory.py` — attach `_cubeplex_provider` / `_cubeplex_model_id` to LLM instances
- `backend/cubeplex/agents/graph.py` — accept `billing_repo` + `user_id`, mount `CostMiddleware`
- `backend/cubeplex/middleware/subagents.py` — clone `CostMiddleware` for child agents
- `backend/cubeplex/streams/run_manager.py` — create `BillingRepository`, pass to `create_cubeplex_agent`
- `backend/cubeplex/api/routes/v1/admin.py` — `include_router(cost_router)`
- `backend/cubeplex/models/__init__.py` — export `BillingEvent`, `LlmBillingEvent`
- `backend/cubeplex/repositories/__init__.py` — export `BillingRepository`
- `frontend/packages/web/components/admin/AdminSubNav.tsx` — add "成本" nav item
- `frontend/packages/core/src/index.ts` — export billing types + API client

---

## Task 1: SQLModel Models + Alembic Migration

**Files:**
- Create: `backend/cubeplex/models/billing.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Create: `backend/alembic/versions/<hash>_add_billing_tables.py` (generated)

- [ ] **Step 1: Create the model file**

```python
# backend/cubeplex/models/billing.py
"""Billing models — parent/child tables for cost tracking."""

from datetime import UTC, datetime

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.mixins import OrgScopedMixin


class BillingEvent(SQLModel, OrgScopedMixin, table=True):
    """Parent billing row — one per billable event (LLM call, sandbox, storage…)."""

    __tablename__ = "billing_events"
    __table_args__ = (
        Index("ix_billing_events_org_ws_time", "org_id", "workspace_id", "started_at"),
        Index("ix_billing_events_org_time", "org_id", "started_at"),
        Index("ix_billing_events_conversation", "conversation_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    user_id: str = Field(max_length=36, index=True)
    conversation_id: str = Field(max_length=36)
    event_type: str = Field(max_length=32)          # "llm_call" | "sandbox_compute" | …
    cost_amount_micro: int = Field(default=0)        # amount × 10⁶ in `currency`
    currency: str = Field(default="USD", max_length=3)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0)
    status: str = Field(max_length=20)              # "success" | "error" | "fallback_failed"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LlmBillingEvent(SQLModel, table=True):
    """Child row for LLM-specific fields (JOINed with BillingEvent)."""

    __tablename__ = "billing_llm_events"
    __table_args__ = (
        Index("ix_billing_llm_provider_model", "provider", "model_id"),
        Index("ix_billing_llm_parent", "parent_run_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    billing_event_id: str = Field(
        max_length=36, foreign_key="billing_events.id", index=True
    )
    provider: str = Field(max_length=64)
    model_id: str = Field(max_length=128)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)
    cache_write_tokens: int = Field(default=0)
    price_input_per_mtok_micro: int = Field(default=0)    # snapshot at write time
    price_output_per_mtok_micro: int = Field(default=0)
    price_cache_read_per_mtok_micro: int = Field(default=0)
    price_cache_write_per_mtok_micro: int = Field(default=0)
    parent_run_id: str | None = Field(default=None, max_length=36)   # set for subagent calls
    subagent_depth: int = Field(default=0)
    error_class: str | None = Field(default=None, max_length=128)
```

- [ ] **Step 2: Export from models `__init__.py`**

Open `backend/cubeplex/models/__init__.py`. Add to imports and `__all__`:

```python
from cubeplex.models.billing import BillingEvent, LlmBillingEvent
```

Add `"BillingEvent"` and `"LlmBillingEvent"` to `__all__` if it exists.

- [ ] **Step 3: Generate Alembic migration**

```bash
cd backend
uv run alembic revision --autogenerate -m "add billing tables"
```

Open the generated file in `backend/alembic/versions/`. Verify it contains `op.create_table("billing_events", ...)` and `op.create_table("billing_llm_events", ...)`. If `downgrade` only has `pass`, add the `op.drop_table` calls manually.

- [ ] **Step 4: Apply migration and confirm tables exist**

```bash
uv run alembic upgrade head
```

Then confirm:
```bash
uv run python -c "
from cubeplex.db.engine import engine
import asyncio
from sqlalchemy import text

async def check():
    async with engine.begin() as conn:
        result = await conn.execute(text('SHOW TABLES LIKE \"billing%\"'))
        print(list(result))
asyncio.run(check())
"
```

Expected: `[('billing_events',), ('billing_llm_events',)]`

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/models/billing.py backend/cubeplex/models/__init__.py backend/alembic/versions/
git commit -m "feat(billing): add billing_events + billing_llm_events tables"
```

---

## Task 2: BillingRepository — Insert

**Files:**
- Create: `backend/cubeplex/repositories/billing.py`
- Modify: `backend/cubeplex/repositories/__init__.py`
- Create: `backend/tests/test_billing_repository.py` (partial — insert tests only)

- [ ] **Step 1: Write failing tests for insert**

```python
# backend/tests/test_billing_repository.py
"""Unit tests for BillingRepository."""

import pytest
from datetime import UTC, datetime
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
    defaults = dict(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        conversation_id="conv-1",
        event_type="llm_call",
        cost_amount_micro=1500,
        currency="USD",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_ms=300,
        status="success",
    )
    return BillingEvent(**{**defaults, **kwargs})


def _make_llm_event(billing_event_id: str, **kwargs) -> LlmBillingEvent:
    defaults = dict(
        billing_event_id=billing_event_id,
        provider="openai",
        model_id="gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
        price_input_per_mtok_micro=150_000,
        price_output_per_mtok_micro=600_000,
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run pytest tests/test_billing_repository.py::test_insert_llm_event_persists_both_rows tests/test_billing_repository.py::test_record_fallback_failure_writes_error_row -v
```

Expected: `ImportError` or `ModuleNotFoundError` (BillingRepository not yet created).

- [ ] **Step 3: Implement BillingRepository insert methods**

```python
# backend/cubeplex/repositories/billing.py
"""BillingRepository — insert and query billing_events + billing_llm_events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from cubeplex.models.billing import BillingEvent, LlmBillingEvent


class BillingRepository:
    """Handles all reads and writes for the billing tables.

    org_id is required at construction; workspace_id is passed per-query
    so the same repo instance can serve both workspace-scoped and org-wide queries.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def insert_llm_event(
        self, billing_evt: BillingEvent, llm_evt: LlmBillingEvent
    ) -> None:
        """Insert parent + child rows in the same transaction."""
        self.session.add(billing_evt)
        self.session.add(llm_evt)
        await self.session.commit()

    async def record_fallback_failure(
        self,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str,
        provider: str,
        model_id: str,
        started_at: datetime,
        ended_at: datetime,
        error_class: str,
    ) -> None:
        """Write a billing row for a failed primary hop in a fallback chain."""
        duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))
        be = BillingEvent(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="llm_call",
            cost_amount_micro=0,
            currency="USD",
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            status="fallback_failed",
        )
        le = LlmBillingEvent(
            billing_event_id=be.id,
            provider=provider,
            model_id=model_id,
            input_tokens=0,
            output_tokens=0,
            error_class=error_class,
        )
        self.session.add(be)
        self.session.add(le)
        await self.session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_billing_repository.py::test_insert_llm_event_persists_both_rows tests/test_billing_repository.py::test_record_fallback_failure_writes_error_row -v
```

Expected: `2 passed`.

- [ ] **Step 5: Export from repositories `__init__.py`**

Open `backend/cubeplex/repositories/__init__.py` and add:

```python
from cubeplex.repositories.billing import BillingRepository
```

Add `"BillingRepository"` to `__all__` if it exists.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/repositories/billing.py backend/cubeplex/repositories/__init__.py backend/tests/test_billing_repository.py
git commit -m "feat(billing): BillingRepository insert + fallback failure recording"
```

---

## Task 3: BillingRepository — Aggregate Queries

**Files:**
- Modify: `backend/cubeplex/repositories/billing.py` (add query methods)
- Modify: `backend/tests/test_billing_repository.py` (add query tests)

- [ ] **Step 1: Write failing tests for aggregation queries**

Append to `backend/tests/test_billing_repository.py`:

```python
async def test_get_workspace_spend_sums_by_day(session: AsyncSession) -> None:
    repo = BillingRepository(session, org_id="org-1")
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)

    for i in range(3):
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run pytest tests/test_billing_repository.py -k "spend or export" -v
```

Expected: `AttributeError` (methods not defined yet).

- [ ] **Step 3: Implement aggregate query methods**

Append to `BillingRepository` class in `backend/cubeplex/repositories/billing.py`:

```python
    async def get_workspace_spend(
        self,
        *,
        workspace_id: str,
        since: datetime,
        until: datetime,
        group_by: Literal["user", "model", "day"] = "day",
    ) -> list[dict[str, Any]]:
        """Aggregate billing_events for a single workspace."""
        base = (
            select(
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),
                func.sum(LlmBillingEvent.input_tokens).label("input_tokens"),
                func.sum(LlmBillingEvent.output_tokens).label("output_tokens"),
                func.sum(LlmBillingEvent.cache_read_tokens).label("cache_read_tokens"),
                func.sum(LlmBillingEvent.cache_write_tokens).label("cache_write_tokens"),
                BillingEvent.currency,
            )
            .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
            .where(
                BillingEvent.org_id == self.org_id,
                BillingEvent.workspace_id == workspace_id,
                BillingEvent.started_at >= since,
                BillingEvent.started_at <= until,
                BillingEvent.event_type == "llm_call",
            )
        )
        if group_by == "day":
            from sqlalchemy import func as f
            bucket_col = f.date(BillingEvent.started_at).label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                f.date(BillingEvent.started_at), BillingEvent.currency
            )
        elif group_by == "user":
            bucket_col = BillingEvent.user_id.label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.user_id, BillingEvent.currency
            )
        else:  # model
            from sqlalchemy import literal_column
            bucket_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(
                "bucket"
            )
            stmt = base.add_columns(bucket_col).group_by(
                LlmBillingEvent.provider, LlmBillingEvent.model_id, BillingEvent.currency
            )

        result = await self.session.execute(stmt)
        return [
            {
                "bucket": str(row.bucket),
                "bucket_type": group_by,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cache_read_tokens": row.cache_read_tokens or 0,
                "cache_write_tokens": row.cache_write_tokens or 0,
                "cost_amount_micro": row.cost or 0,
                "currency": row.currency,
                "call_count": row.calls or 0,
            }
            for row in result
        ]

    async def get_org_spend(
        self,
        *,
        since: datetime,
        until: datetime,
        group_by: Literal["workspace", "user", "model", "day"] = "workspace",
    ) -> list[dict[str, Any]]:
        """Aggregate billing_events across all workspaces in the org."""
        base = (
            select(
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),
                func.sum(LlmBillingEvent.input_tokens).label("input_tokens"),
                func.sum(LlmBillingEvent.output_tokens).label("output_tokens"),
                func.sum(LlmBillingEvent.cache_read_tokens).label("cache_read_tokens"),
                func.sum(LlmBillingEvent.cache_write_tokens).label("cache_write_tokens"),
                BillingEvent.currency,
            )
            .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
            .where(
                BillingEvent.org_id == self.org_id,
                BillingEvent.started_at >= since,
                BillingEvent.started_at <= until,
                BillingEvent.event_type == "llm_call",
            )
        )
        if group_by == "workspace":
            bucket_col = BillingEvent.workspace_id.label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.workspace_id, BillingEvent.currency
            )
        elif group_by == "user":
            bucket_col = BillingEvent.user_id.label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.user_id, BillingEvent.currency
            )
        elif group_by == "day":
            from sqlalchemy import func as f
            bucket_col = f.date(BillingEvent.started_at).label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                f.date(BillingEvent.started_at), BillingEvent.currency
            )
        else:  # model
            bucket_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(
                "bucket"
            )
            stmt = base.add_columns(bucket_col).group_by(
                LlmBillingEvent.provider, LlmBillingEvent.model_id, BillingEvent.currency
            )

        result = await self.session.execute(stmt)
        return [
            {
                "bucket": str(row.bucket),
                "bucket_type": group_by,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cache_read_tokens": row.cache_read_tokens or 0,
                "cache_write_tokens": row.cache_write_tokens or 0,
                "cost_amount_micro": row.cost or 0,
                "currency": row.currency,
                "call_count": row.calls or 0,
            }
            for row in result
        ]

    async def stream_events_for_export(
        self,
        *,
        since: datetime,
        until: datetime,
        workspace_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream flat join rows for CSV export (lazy cursor, no full load)."""
        stmt = (
            select(
                BillingEvent.id,
                BillingEvent.started_at,
                BillingEvent.workspace_id,
                BillingEvent.user_id,
                BillingEvent.conversation_id,
                BillingEvent.cost_amount_micro,
                BillingEvent.currency,
                BillingEvent.status,
                BillingEvent.duration_ms,
                LlmBillingEvent.provider,
                LlmBillingEvent.model_id,
                LlmBillingEvent.input_tokens,
                LlmBillingEvent.output_tokens,
                LlmBillingEvent.cache_read_tokens,
                LlmBillingEvent.cache_write_tokens,
                LlmBillingEvent.subagent_depth,
            )
            .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
            .where(
                BillingEvent.org_id == self.org_id,
                BillingEvent.started_at >= since,
                BillingEvent.started_at <= until,
                BillingEvent.event_type == "llm_call",
            )
            .order_by(BillingEvent.started_at)
        )
        if workspace_id is not None:
            stmt = stmt.where(BillingEvent.workspace_id == workspace_id)

        result = await self.session.stream(stmt)
        async for row in result:
            yield {
                "id": row.id,
                "started_at": row.started_at.isoformat(),
                "workspace_id": row.workspace_id,
                "user_id": row.user_id,
                "conversation_id": row.conversation_id,
                "provider": row.provider,
                "model_id": row.model_id,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cache_read_tokens": row.cache_read_tokens,
                "cache_write_tokens": row.cache_write_tokens,
                "cost_amount": row.cost_amount_micro / 1_000_000,
                "currency": row.currency,
                "status": row.status,
                "subagent_depth": row.subagent_depth,
                "duration_ms": row.duration_ms,
            }
```

- [ ] **Step 4: Run all repository tests**

```bash
cd backend
uv run pytest tests/test_billing_repository.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/repositories/billing.py backend/tests/test_billing_repository.py
git commit -m "feat(billing): BillingRepository aggregate queries and export stream"
```

---

## Task 4: LLMFactory Changes

**Files:**
- Modify: `backend/cubeplex/llm/config.py`
- Modify: `backend/cubeplex/llm/factory.py`

- [ ] **Step 1: Add `currency` to `ModelCost`**

In `backend/cubeplex/llm/config.py`, add `currency` field to `ModelCost`:

```python
class ModelCost(BaseModel):
    """Cost configuration for a model"""

    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    input: float = Field(description="Input token cost per million tokens")
    output: float = Field(description="Output token cost per million tokens")
    cache_read: float = Field(
        default=0, description="Cache read cost per million tokens", alias="cache_read"
    )
    cache_write: float = Field(
        default=0,
        description="Cache write cost per million tokens",
        alias="cache_write",
    )
```

- [ ] **Step 2: Attach provider/model metadata to LLM instances**

In `backend/cubeplex/llm/factory.py`, in the `create()` method, just before the `return` statements, add the attribute assignment.

Find the two return sites (`return ChatOpenAI(...)` and `return ChatOpenAICompatible(...)`). Replace with:

```python
        if is_official_openai:
            if reasoning_config:
                llm_kwargs["reasoning"] = reasoning_config
            elif use_responses_api:
                llm_kwargs["use_responses_api"] = True
            llm = ChatOpenAI(**llm_kwargs)
        else:
            llm = ChatOpenAICompatible(**llm_kwargs)

        # Attach cubeplex metadata for CostMiddleware to read
        llm._cubeplex_provider = provider_name      # type: ignore[attr-defined]
        llm._cubeplex_model_id = model_config.id    # type: ignore[attr-defined]
        llm._cubeplex_model_cost = model_config.cost  # type: ignore[attr-defined]
        return llm
```

Also do the same for the `ChatOpenAICompatible` path (merge the two return paths above into one as shown).

For the Anthropic path, add before the `raise NotImplementedError`:
```python
        # (future: attach same attrs when Anthropic is implemented)
```

- [ ] **Step 3: Run type check to confirm no regressions**

```bash
cd backend
uv run mypy cubeplex/llm/
```

Expected: `Success: no issues found`.

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/llm/config.py backend/cubeplex/llm/factory.py
git commit -m "feat(billing): attach provider/model/cost metadata to LLM instances"
```

---

## Task 5: CostMiddleware

**Files:**
- Create: `backend/cubeplex/middleware/cost.py`
- Create: `backend/tests/test_cost_middleware.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_cost_middleware.py
"""Unit tests for CostMiddleware."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain.agents.middleware.types import ModelRequest, ModelResponse

from cubeplex.middleware.cost import CostMiddleware


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
    llm._cubeplex_provider = provider
    llm._cubeplex_model_id = model_id
    llm._cubeplex_model_cost = _make_model_cost()
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
    llm._cubeplex_model_cost = _make_model_cost(input=0.15, output=0.60)

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run pytest tests/test_cost_middleware.py -v
```

Expected: `ImportError` (module not created yet).

- [ ] **Step 3: Implement CostMiddleware**

```python
# backend/cubeplex/middleware/cost.py
"""CostMiddleware — records per-LLM-call billing events."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Sequence
from uuid_utils import uuid7

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from loguru import logger

from cubeplex.models.billing import BillingEvent, LlmBillingEvent


class CostMiddleware(AgentMiddleware[Any, Any, Any]):
    """Records one billing_events + billing_llm_events row per LLM call."""

    tools: Sequence[BaseTool] = []

    def __init__(
        self,
        *,
        repo: Any,                   # BillingRepository; Any to avoid circular import
        org_id: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str,
        parent_billing_id: str | None = None,
        subagent_depth: int = 0,
    ) -> None:
        self._repo = repo
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._parent_billing_id = parent_billing_id
        self._subagent_depth = subagent_depth
        self._last_billing_id: str | None = None   # set after each call; read by subagent clone

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        run_id = str(uuid7())
        self._last_billing_id = run_id
        started_at = datetime.now(UTC)

        try:
            response = await handler(request)
            ended_at = datetime.now(UTC)
            asyncio.create_task(
                self._write(request, response, run_id, started_at, ended_at, "success", None)
            )
            return response
        except Exception as exc:
            ended_at = datetime.now(UTC)
            asyncio.create_task(
                self._write(
                    request, None, run_id, started_at, ended_at, "error", type(exc).__name__
                )
            )
            raise

    async def _write(
        self,
        request: ModelRequest[Any],
        response: ModelResponse[Any] | AIMessage | None,
        run_id: str,
        started_at: datetime,
        ended_at: datetime,
        status: str,
        error_class: str | None,
    ) -> None:
        from cubeplex.db.engine import async_session_maker
        from cubeplex.repositories.billing import BillingRepository

        try:
            provider, model_id, model_cost = _extract_model_meta(request.model)
            usage = _extract_usage(response)
            cost_micro = _compute_cost_micro(usage, model_cost)
            duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

            be = BillingEvent(
                id=run_id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                event_type="llm_call",
                cost_amount_micro=cost_micro,
                currency=getattr(model_cost, "currency", "USD") if model_cost else "USD",
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                status=status,
            )
            le = LlmBillingEvent(
                billing_event_id=run_id,
                provider=provider,
                model_id=model_id,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                price_input_per_mtok_micro=int(getattr(model_cost, "input", 0) * 1_000_000)
                if model_cost else 0,
                price_output_per_mtok_micro=int(getattr(model_cost, "output", 0) * 1_000_000)
                if model_cost else 0,
                price_cache_read_per_mtok_micro=int(
                    getattr(model_cost, "cache_read", 0) * 1_000_000
                ) if model_cost else 0,
                price_cache_write_per_mtok_micro=int(
                    getattr(model_cost, "cache_write", 0) * 1_000_000
                ) if model_cost else 0,
                parent_run_id=self._parent_billing_id,
                subagent_depth=self._subagent_depth,
                error_class=error_class,
            )

            async with async_session_maker() as session:
                repo = BillingRepository(session, org_id=self._org_id)
                await repo.insert_llm_event(be, le)

        except Exception as exc:
            logger.warning("billing write failed (run_id={}): {}", run_id, exc)


def _extract_model_meta(model: Any) -> tuple[str, str, Any]:
    provider = getattr(model, "_cubeplex_provider", "unknown")
    model_id = getattr(model, "_cubeplex_model_id", "unknown")
    model_cost = getattr(model, "_cubeplex_model_cost", None)
    return provider, model_id, model_cost


def _extract_usage(response: Any) -> dict[str, int]:
    if response is None:
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}

    result = getattr(response, "result", response)
    usage = getattr(result, "usage_metadata", None) or {}
    if callable(usage):
        usage = {}

    details_in = usage.get("input_token_details") or {}
    details_out = usage.get("output_token_details") or {}

    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": details_in.get("cache_read", 0),
        "cache_write_tokens": details_out.get("cache_write", 0),
    }


def _compute_cost_micro(usage: dict[str, int], cost: Any) -> int:
    if cost is None:
        return 0
    total = (
        usage["input_tokens"] * getattr(cost, "input", 0) / 1_000_000
        + usage["output_tokens"] * getattr(cost, "output", 0) / 1_000_000
        + usage["cache_read_tokens"] * getattr(cost, "cache_read", 0) / 1_000_000
        + usage["cache_write_tokens"] * getattr(cost, "cache_write", 0) / 1_000_000
    )
    return int(total * 1_000_000)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend
uv run pytest tests/test_cost_middleware.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Run full unit test suite to confirm no regressions**

```bash
uv run pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -20
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/middleware/cost.py backend/tests/test_cost_middleware.py
git commit -m "feat(billing): CostMiddleware with fire-and-forget billing write"
```

---

## Task 6: Wire create_cubeplex_agent + RunManager

**Files:**
- Modify: `backend/cubeplex/agents/graph.py`
- Modify: `backend/cubeplex/streams/run_manager.py`

- [ ] **Step 1: Add billing params to `create_cubeplex_agent`**

In `backend/cubeplex/agents/graph.py`, update the function signature and body:

```python
def create_cubeplex_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    conversation_id: str | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,          # ← add
    billing_repo: Any | None = None,     # ← add (Any avoids circular import)
    skills: list[SkillSpec] | None = None,
    subagents: list[SubAgent] | None = None,
    checkpointer: Checkpointer | None = None,
    citation_configs: dict[str, CitationConfig] | None = None,
    event_queue: asyncio.Queue[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
```

Inside the function body, after the existing middleware setup and before `create_agent(...)`, add `CostMiddleware` mounting:

```python
    # Mount CostMiddleware last in the chain so it wraps all model calls
    if billing_repo is not None and user_id is not None and conversation_id is not None:
        from cubeplex.middleware.cost import CostMiddleware
        cost_mw = CostMiddleware(
            repo=billing_repo,
            org_id=org_id or "",
            workspace_id=workspace_id or "",
            user_id=user_id,
            conversation_id=conversation_id,
        )
        middleware.append(cost_mw)
```

- [ ] **Step 2: Wire billing in `run_manager.py`**

In `backend/cubeplex/streams/run_manager.py`, find the block around line 475 where `create_cubeplex_agent` is called. Add billing repo creation just before the call:

```python
            from cubeplex.agents.graph import create_cubeplex_agent
            from cubeplex.llm.factory import LLMFactory
            from cubeplex.middleware.citations import CitationConfig, load_citation_configs
            from cubeplex.tools import get_registry

            # Create a billing repo for this run (owns its own DB session lifecycle)
            billing_repo = None
            try:
                from cubeplex.repositories.billing import BillingRepository
                from cubeplex.db.engine import async_session_maker as _asm
                _billing_session = await _asm().__aenter__()
                billing_repo = BillingRepository(_billing_session, org_id=ctx.org_id)
            except Exception as _be:
                logger.warning("billing repo init failed, continuing without: {}", _be)
```

Then update the `create_cubeplex_agent(...)` call to pass the new params:

```python
            agent = create_cubeplex_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                conversation_id=conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,              # ← add
                billing_repo=billing_repo,         # ← add
                skills=ctx.skills,
                checkpointer=checkpointer,
                citation_configs=all_citation_configs,
                event_queue=event_q,
            )
```

Also close the billing session when the run ends. Find the `finally:` block of the run task and add:

```python
            finally:
                if billing_repo is not None:
                    try:
                        await billing_repo.session.aclose()
                    except Exception:
                        pass
```

- [ ] **Step 3: Run type check**

```bash
cd backend
uv run mypy cubeplex/agents/graph.py cubeplex/streams/run_manager.py
```

Expected: `Success: no issues found` (or only pre-existing errors).

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/agents/graph.py backend/cubeplex/streams/run_manager.py
git commit -m "feat(billing): wire CostMiddleware into create_cubeplex_agent + RunManager"
```

---

## Task 7: Subagent Clone

**Files:**
- Modify: `backend/cubeplex/middleware/subagents.py`

- [ ] **Step 1: Find the subagent tool creation in `_create_subagent_tool`**

Open `backend/cubeplex/middleware/subagents.py`. In `_run_subagent`, after `spec = subagent_map.get(...)` and before `agent = create_agent(...)`, add:

```python
        # Inherit and deepen CostMiddleware for billing attribution
        from cubeplex.middleware.cost import CostMiddleware
        _cost_mw: CostMiddleware | None = None
        for mw in (inherited_middleware or []):
            if isinstance(mw, CostMiddleware):
                _cost_mw = mw
                break

        if _cost_mw is not None:
            child_cost_mw = CostMiddleware(
                repo=_cost_mw._repo,
                org_id=_cost_mw._org_id,
                workspace_id=_cost_mw._workspace_id,
                user_id=_cost_mw._user_id,
                conversation_id=_cost_mw._conversation_id,
                parent_billing_id=_cost_mw._last_billing_id,
                subagent_depth=_cost_mw._subagent_depth + 1,
            )
            middleware = [child_cost_mw] + [m for m in middleware if not isinstance(m, CostMiddleware)]
```

- [ ] **Step 2: Run the existing subagent-related tests to confirm no regressions**

```bash
cd backend
uv run pytest tests/ --ignore=tests/e2e -k "subagent or agent" -v 2>&1 | tail -20
```

Expected: all pass (or same pass/fail ratio as before this task).

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/middleware/subagents.py
git commit -m "feat(billing): clone CostMiddleware for subagent depth tracking"
```

---

## Task 8: Admin API — Schemas + Routes

**Files:**
- Create: `backend/cubeplex/api/schemas/billing.py`
- Create: `backend/cubeplex/api/routes/v1/cost.py`
- Modify: `backend/cubeplex/api/routes/v1/admin.py`

- [ ] **Step 1: Create response schemas**

```python
# backend/cubeplex/api/schemas/billing.py
"""Pydantic schemas for billing/cost API responses."""

from datetime import date
from pydantic import BaseModel


class CostAggregateRow(BaseModel):
    bucket: str              # workspace_id | user_id | "provider/model_id" | "YYYY-MM-DD"
    bucket_type: str         # "workspace" | "user" | "model" | "day"
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_amount_micro: int   # amount × 10⁶; divide by 1_000_000 for display
    currency: str
    call_count: int


class CostSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_cost_amount_micro: int
    currency: str
    total_calls: int
    by_workspace: list[CostAggregateRow]
    by_model: list[CostAggregateRow]
    by_day: list[CostAggregateRow]
```

- [ ] **Step 2: Create cost routes**

```python
# backend/cubeplex/api/routes/v1/cost.py
"""Admin cost/billing endpoints. All routes require org-admin access."""

import csv
import io
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.billing import CostAggregateRow, CostSummaryResponse
from cubeplex.auth.dependencies import current_active_user, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import BillingRepository, MembershipRepository

router = APIRouter(prefix="/cost", tags=["cost"])


async def _require_org_admin(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[User, str]:
    """Returns (user, org_id) after verifying org-admin access."""
    from fastapi import HTTPException
    org_id = await resolve_current_org_id(user, session)
    is_admin = await MembershipRepository(session).user_has_role_in_org(
        user_id=user.id, org_id=org_id, role=Role.ADMIN
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="org admin required")
    return user, org_id


def _parse_date_range(
    from_date: str | None,
    to_date: str | None,
) -> tuple[datetime, datetime]:
    today = date.today()
    since_d = date(today.year, today.month, 1) if from_date is None else date.fromisoformat(from_date)
    until_d = today if to_date is None else date.fromisoformat(to_date)
    since = datetime(since_d.year, since_d.month, since_d.day, tzinfo=UTC)
    until = datetime(until_d.year, until_d.month, until_d.day, 23, 59, 59, tzinfo=UTC)
    return since, until


@router.get("/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> CostSummaryResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    by_workspace = await repo.get_org_spend(since=since, until=until, group_by="workspace")
    by_model = await repo.get_org_spend(since=since, until=until, group_by="model")
    by_day = await repo.get_org_spend(since=since, until=until, group_by="day")

    total_cost = sum(r["cost_amount_micro"] for r in by_workspace)
    total_calls = sum(r["call_count"] for r in by_workspace)
    # All rows share the same currency in org (v1 assumption); default to USD if empty
    currency = by_workspace[0]["currency"] if by_workspace else "USD"

    return CostSummaryResponse(
        from_date=since.date(),
        to_date=until.date(),
        total_cost_amount_micro=total_cost,
        currency=currency,
        total_calls=total_calls,
        by_workspace=[CostAggregateRow(**r) for r in by_workspace],
        by_model=[CostAggregateRow(**r) for r in by_model],
        by_day=[CostAggregateRow(**r) for r in by_day],
    )


@router.get("/by-workspace/{ws_id}", response_model=list[CostAggregateRow])
async def get_workspace_cost(
    ws_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    group_by: str = Query(default="day"),
) -> list[CostAggregateRow]:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)
    rows = await repo.get_workspace_spend(
        workspace_id=ws_id, since=since, until=until,
        group_by=group_by,  # type: ignore[arg-type]
    )
    return [CostAggregateRow(**r) for r in rows]


@router.get("/export.csv")
async def export_org_csv(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> StreamingResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    async def _generate():
        yield ",".join([
            "started_at", "workspace_id", "user_id", "conversation_id",
            "provider", "model_id",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "cost_amount", "currency", "status", "subagent_depth", "duration_ms",
        ]) + "\n"
        async for row in repo.stream_events_for_export(since=since, until=until):
            yield ",".join(str(row[k]) for k in [
                "started_at", "workspace_id", "user_id", "conversation_id",
                "provider", "model_id",
                "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                "cost_amount", "currency", "status", "subagent_depth", "duration_ms",
            ]) + "\n"

    filename = f"cost_{since.strftime('%Y-%m')}_{org_id[:8]}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/by-workspace/{ws_id}/export.csv")
async def export_workspace_csv(
    ws_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> StreamingResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    async def _generate():
        yield ",".join([
            "started_at", "workspace_id", "user_id", "conversation_id",
            "provider", "model_id",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "cost_amount", "currency", "status", "subagent_depth", "duration_ms",
        ]) + "\n"
        async for row in repo.stream_events_for_export(
            since=since, until=until, workspace_id=ws_id
        ):
            yield ",".join(str(row[k]) for k in [
                "started_at", "workspace_id", "user_id", "conversation_id",
                "provider", "model_id",
                "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                "cost_amount", "currency", "status", "subagent_depth", "duration_ms",
            ]) + "\n"

    filename = f"cost_{since.strftime('%Y-%m')}_{ws_id[:8]}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 3: Include cost router in admin routes**

Open `backend/cubeplex/api/routes/v1/admin.py`. At the bottom, add:

```python
from cubeplex.api.routes.v1.cost import router as cost_router

router.include_router(cost_router)
```

- [ ] **Step 4: Run type check**

```bash
cd backend
uv run mypy cubeplex/api/routes/v1/cost.py cubeplex/api/schemas/billing.py
```

Expected: `Success: no issues found`.

- [ ] **Step 5: Smoke-test the routes with curl (server must be running)**

```bash
# Start server in background: cd backend && python main.py &
curl -s -b "cubeplex_auth=<token>" http://localhost:8000/api/v1/admin/cost/summary | python3 -m json.tool
```

Expected: JSON with `from_date`, `to_date`, `total_calls: 0`, `by_workspace: []` (empty DB is fine).

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/cubeplex/api/schemas/billing.py backend/cubeplex/api/routes/v1/cost.py backend/cubeplex/api/routes/v1/admin.py
git commit -m "feat(billing): admin cost API endpoints (summary, by-workspace, CSV export)"
```

---

## Task 9: Backend E2E Test

**Files:**
- Create: `backend/tests/e2e/test_billing.py`

> ⚠️ Before running: copy `.env` and `config.development.local.yaml` into this worktree's `backend/` if not already present (see `backend/CLAUDE.md` "Running E2E tests locally").

- [ ] **Step 1: Create the E2E test**

```python
# backend/tests/e2e/test_billing.py
"""E2E tests for billing — assert billing rows are written on real LLM calls."""

import asyncio
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.billing import BillingEvent, LlmBillingEvent


async def _count_billing_rows(session: AsyncSession, conversation_id: str) -> int:
    result = await session.execute(
        select(func.count()).where(
            BillingEvent.conversation_id == conversation_id,
            BillingEvent.event_type == "llm_call",
        )
    )
    return result.scalar_one()


@pytest.mark.e2e
async def test_send_message_creates_billing_event(
    http_client,  # fixture from conftest.py — authenticated AsyncClient
    db_session,   # fixture from conftest.py — AsyncSession
    test_workspace_id: str,
) -> None:
    """Sending a message triggers at least one billing_events row."""
    # Create a conversation
    resp = await http_client.post(
        f"/api/v1/ws/{test_workspace_id}/conversations",
        params={"title": "billing test"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    # Send a short message and stream until done
    async with http_client.stream(
        "POST",
        f"/api/v1/ws/{test_workspace_id}/conversations/{conv_id}/messages",
        json={"content": "Say exactly: hello"},
        headers={"accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if '"type":"done"' in line or line.strip() == "":
                break

    # Give fire-and-forget write a moment to complete
    await asyncio.sleep(0.5)

    count = await _count_billing_rows(db_session, conv_id)
    assert count >= 1, "Expected at least one billing row after LLM call"

    # Verify child row exists and has non-zero tokens
    result = await db_session.execute(
        select(BillingEvent, LlmBillingEvent)
        .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
        .where(BillingEvent.conversation_id == conv_id)
    )
    rows = result.all()
    assert len(rows) >= 1
    _be, le = rows[0]
    assert le.input_tokens > 0
    assert le.provider != "unknown"
    assert le.model_id != "unknown"
    assert _be.cost_amount_micro >= 0  # ≥ 0; may be 0 if model cost not configured


@pytest.mark.e2e
async def test_cost_summary_endpoint_returns_data(
    http_client,
    test_workspace_id: str,
) -> None:
    """After a message is sent, /admin/cost/summary returns non-zero total_calls."""
    # Send a message first (re-uses any existing conv from the test DB)
    resp = await http_client.post(
        f"/api/v1/ws/{test_workspace_id}/conversations",
        params={"title": "cost summary test"},
    )
    conv_id = resp.json()["id"]
    async with http_client.stream(
        "POST",
        f"/api/v1/ws/{test_workspace_id}/conversations/{conv_id}/messages",
        json={"content": "Say: ok"},
        headers={"accept": "text/event-stream"},
    ) as r:
        async for line in r.aiter_lines():
            if '"type":"done"' in line:
                break

    await asyncio.sleep(0.5)

    summary = await http_client.get("/api/v1/admin/cost/summary")
    assert summary.status_code == 200
    data = summary.json()
    assert data["total_calls"] >= 1
```

- [ ] **Step 2: Run E2E test**

```bash
cd backend
uv run pytest tests/e2e/test_billing.py -v -s
```

Expected: `2 passed`. If you see `total_calls: 0` but count passed, check that the `BillingRepository` in the test is using the same DB as the running server.

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add backend/tests/e2e/test_billing.py
git commit -m "test(billing): E2E tests for billing row creation and cost summary endpoint"
```

---

## Task 10: Frontend — Core Types + API Client

**Files:**
- Create: `frontend/packages/core/src/types/billing.ts`
- Create: `frontend/packages/core/src/api/billing.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Create TypeScript types**

```typescript
// frontend/packages/core/src/types/billing.ts
export interface CostAggregateRow {
  bucket: string;
  bucket_type: 'workspace' | 'user' | 'model' | 'day';
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_amount_micro: number;
  currency: string;
  call_count: number;
}

export interface CostSummaryResponse {
  from_date: string;   // "YYYY-MM-DD"
  to_date: string;
  total_cost_amount_micro: number;
  currency: string;
  total_calls: number;
  by_workspace: CostAggregateRow[];
  by_model: CostAggregateRow[];
  by_day: CostAggregateRow[];
}

export function formatCostUsd(micro: number, currency: string = 'USD'): string {
  const amount = micro / 1_000_000;
  return `${currency} ${amount.toFixed(4)}`;
}
```

- [ ] **Step 2: Create API client**

```typescript
// frontend/packages/core/src/api/billing.ts
import type { CostSummaryResponse, CostAggregateRow } from '../types/billing';

export async function fetchCostSummary(
  params: { from?: string; to?: string } = {}
): Promise<CostSummaryResponse> {
  const query = new URLSearchParams();
  if (params.from) query.set('from_date', params.from);
  if (params.to) query.set('to_date', params.to);

  const resp = await fetch(`/api/v1/admin/cost/summary?${query}`, {
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(`cost summary failed: ${resp.status}`);
  return resp.json() as Promise<CostSummaryResponse>;
}

export async function fetchWorkspaceCost(
  wsId: string,
  params: { from?: string; to?: string; group_by?: string } = {}
): Promise<CostAggregateRow[]> {
  const query = new URLSearchParams();
  if (params.from) query.set('from_date', params.from);
  if (params.to) query.set('to_date', params.to);
  if (params.group_by) query.set('group_by', params.group_by);

  const resp = await fetch(`/api/v1/admin/cost/by-workspace/${wsId}?${query}`, {
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(`workspace cost failed: ${resp.status}`);
  return resp.json() as Promise<CostAggregateRow[]>;
}

export function buildExportUrl(wsId?: string, params: { from?: string; to?: string } = {}): string {
  const query = new URLSearchParams();
  if (params.from) query.set('from_date', params.from);
  if (params.to) query.set('to_date', params.to);
  const base = wsId
    ? `/api/v1/admin/cost/by-workspace/${wsId}/export.csv`
    : '/api/v1/admin/cost/export.csv';
  return `${base}?${query}`;
}
```

- [ ] **Step 3: Export from core index**

Open `frontend/packages/core/src/index.ts`. Add:

```typescript
export type { CostAggregateRow, CostSummaryResponse } from './types/billing';
export { formatCostUsd } from './types/billing';
export { fetchCostSummary, fetchWorkspaceCost, buildExportUrl } from './api/billing';
```

- [ ] **Step 4: Build core and run type check**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm type-check
```

Expected: `0 errors`.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add frontend/packages/core/src/types/billing.ts frontend/packages/core/src/api/billing.ts frontend/packages/core/src/index.ts
git commit -m "feat(billing): frontend core types + billing API client"
```

---

## Task 11: Frontend — Cost Page + AdminSubNav

**Files:**
- Create: `frontend/packages/web/app/admin/cost/page.tsx`
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`

- [ ] **Step 1: Add "成本" nav item to AdminSubNav**

Open `frontend/packages/web/components/admin/AdminSubNav.tsx`. Add after the Sandbox `<NavItem>`:

```tsx
<NavItem href="/admin/cost" icon={CircleDollarSign}>成本</NavItem>
```

Import `CircleDollarSign` from `lucide-react` at the top:

```tsx
import { Box, CircleDollarSign, Cpu, Globe, Plug, Sparkles } from 'lucide-react';
```

- [ ] **Step 2: Create the cost page**

```tsx
// frontend/packages/web/app/admin/cost/page.tsx
'use client';

import { useEffect, useState } from 'react';
import { buildExportUrl, fetchCostSummary, formatCostUsd } from '@cubeplex/core';
import type { CostSummaryResponse } from '@cubeplex/core';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';

export default function CostPage() {
  const [summary, setSummary] = useState<CostSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCostSummary()
      .then(setSummary)
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 text-muted-foreground">加载中…</div>;
  if (error) return <div className="p-6 text-destructive">{error}</div>;
  if (!summary) return null;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">成本概览</h1>
        <Button variant="outline" size="sm" asChild>
          <a href={buildExportUrl()} download>
            导出全 org CSV ↓
          </a>
        </Button>
      </div>

      {/* Summary */}
      <div className="flex gap-8 rounded-lg border p-4">
        <div>
          <p className="text-sm text-muted-foreground">总花费</p>
          <p className="text-2xl font-mono font-semibold">
            {formatCostUsd(summary.total_cost_amount_micro, summary.currency)}
          </p>
        </div>
        <div>
          <p className="text-sm text-muted-foreground">调用次数</p>
          <p className="text-2xl font-mono font-semibold">
            {summary.total_calls.toLocaleString()}
          </p>
        </div>
      </div>

      {/* By Workspace */}
      <section>
        <h2 className="mb-2 font-medium">按 Workspace</h2>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Workspace</TableHead>
              <TableHead className="text-right">调用次数</TableHead>
              <TableHead className="text-right">Input Tokens</TableHead>
              <TableHead className="text-right">Output Tokens</TableHead>
              <TableHead className="text-right">花费</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {summary.by_workspace.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  本月暂无数据
                </TableCell>
              </TableRow>
            )}
            {summary.by_workspace.map((row) => (
              <TableRow key={row.bucket}>
                <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                <TableCell className="text-right">{row.call_count.toLocaleString()}</TableCell>
                <TableCell className="text-right">{row.input_tokens.toLocaleString()}</TableCell>
                <TableCell className="text-right">{row.output_tokens.toLocaleString()}</TableCell>
                <TableCell className="text-right font-mono">
                  {formatCostUsd(row.cost_amount_micro, row.currency)}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="sm" asChild>
                    <a href={buildExportUrl(row.bucket)} download>
                      CSV ↓
                    </a>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      {/* By Model */}
      <section>
        <h2 className="mb-2 font-medium">按 Model</h2>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Provider / Model</TableHead>
              <TableHead className="text-right">调用次数</TableHead>
              <TableHead className="text-right">Input Tokens</TableHead>
              <TableHead className="text-right">Output Tokens</TableHead>
              <TableHead className="text-right">花费</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {summary.by_model.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  本月暂无数据
                </TableCell>
              </TableRow>
            )}
            {summary.by_model.map((row) => (
              <TableRow key={row.bucket}>
                <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                <TableCell className="text-right">{row.call_count.toLocaleString()}</TableCell>
                <TableCell className="text-right">{row.input_tokens.toLocaleString()}</TableCell>
                <TableCell className="text-right">{row.output_tokens.toLocaleString()}</TableCell>
                <TableCell className="text-right font-mono">
                  {formatCostUsd(row.cost_amount_micro, row.currency)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Type check frontend**

```bash
cd frontend
pnpm type-check
```

Expected: `0 errors`.

- [ ] **Step 4: Start dev server and manually verify the page loads**

```bash
cd frontend
pnpm dev
```

Open `http://localhost:3000/admin/cost` (after logging in as admin). Confirm:
- Page renders with "成本概览" heading
- "按 Workspace" and "按 Model" tables show "本月暂无数据" (empty DB is fine)
- "导出全 org CSV ↓" button is present

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add frontend/packages/web/app/admin/cost/page.tsx frontend/packages/web/components/admin/AdminSubNav.tsx
git commit -m "feat(billing): admin cost page + AdminSubNav 成本 nav item"
```

---

## Task 12: Frontend E2E Test

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/admin-cost.spec.ts`

- [ ] **Step 1: Create the Playwright E2E test**

```typescript
// frontend/packages/web/__tests__/e2e/admin-cost.spec.ts
import { expect, test } from '@playwright/test';

test.describe('Admin cost page', () => {
  test.beforeEach(async ({ page }) => {
    // Reuse auth from existing e2e fixtures (same pattern as admin-console-skeleton.spec.ts)
    await page.goto('/login');
    await page.fill('[name="email"]', process.env.E2E_ADMIN_EMAIL ?? 'admin@example.com');
    await page.fill('[name="password"]', process.env.E2E_ADMIN_PASSWORD ?? 'password');
    await page.click('[type="submit"]');
    await page.waitForURL('**/*', { waitUntil: 'networkidle' });
  });

  test('cost page renders heading and tables', async ({ page }) => {
    await page.goto('/admin/cost');
    await expect(page.getByRole('heading', { name: '成本概览' })).toBeVisible();
    await expect(page.getByText('按 Workspace')).toBeVisible();
    await expect(page.getByText('按 Model')).toBeVisible();
  });

  test('export CSV button returns csv content-type', async ({ page, request }) => {
    // Get auth cookies from logged-in page
    await page.goto('/admin/cost');
    const cookies = await page.context().cookies();
    const cookieStr = cookies.map((c) => `${c.name}=${c.value}`).join('; ');

    const resp = await request.get('/api/v1/admin/cost/export.csv', {
      headers: { Cookie: cookieStr },
    });
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('text/csv');
  });

  test('cost nav item appears in admin sidebar', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('link', { name: '成本' })).toBeVisible();
  });
});
```

- [ ] **Step 2: Run Playwright E2E test**

```bash
cd frontend
pnpm test:e2e --grep "Admin cost page"
```

Expected: `3 passed`. If auth setup differs from other E2E tests, check `admin-console-skeleton.spec.ts` and align the login fixture.

- [ ] **Step 3: Final full test run**

```bash
# Backend unit tests
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking/backend
uv run pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -10

# Backend E2E
uv run pytest tests/e2e/test_billing.py -v

# Frontend type check + build
cd ../frontend
pnpm type-check
pnpm build
```

All should pass.

- [ ] **Step 4: Final commit**

```bash
cd /home/chris/cubeplex/.worktrees/m1-e1-cost-tracking
git add frontend/packages/web/__tests__/e2e/admin-cost.spec.ts
git commit -m "test(billing): Playwright E2E for admin cost page"
```
