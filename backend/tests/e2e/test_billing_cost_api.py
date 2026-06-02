"""E2E tests for the redesigned /admin/cost/* surface and timeseries repo."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models.billing import BillingEvent, LlmBillingEvent
from cubebox.models.conversation import Conversation
from cubebox.models.organization import Organization
from cubebox.models.user import User
from cubebox.models.workspace import Workspace
from cubebox.repositories import BillingRepository
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


@asynccontextmanager
async def _db_session() -> AsyncIterator[AsyncSession]:
    """Create a direct AsyncSession to the test DB (NullPool, no connection sharing)."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def _ensure_org(session: AsyncSession, org_id: str) -> None:
    existing = (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Organization(
                id=org_id,
                name=f"Test {org_id}",
                slug=org_id.replace("_", "-").lower()[:32],
            )
        )
        await session.commit()


async def _ensure_workspace(session: AsyncSession, *, ws_id: str, org_id: str) -> None:
    existing = (
        await session.execute(select(Workspace).where(Workspace.id == ws_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Workspace(id=ws_id, org_id=org_id, name=f"Test {ws_id}"))
        await session.commit()


async def _ensure_user(session: AsyncSession, *, user_id: str) -> None:
    existing = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if existing is None:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@test-billing-cost.local",
                hashed_password="x",
                is_active=True,
            )
        )
        await session.commit()


async def _ensure_conversation(
    session: AsyncSession,
    *,
    conv_id: str,
    org_id: str,
    ws_id: str,
    user_id: str,
) -> None:
    existing = (
        await session.execute(select(Conversation).where(Conversation.id == conv_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Conversation(
                id=conv_id,
                org_id=org_id,
                workspace_id=ws_id,
                creator_user_id=user_id,
                title="Test conv",
            )
        )
        await session.commit()


async def _seed_events(
    session: AsyncSession,
    *,
    org_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """Insert billing rows after ensuring FK parents (org/ws/user/conversation) exist.

    Deletes any prior billing rows for ``org_id`` first so reruns are idempotent
    against a shared dev/test database.
    """
    # Clean prior billing rows for this test org (child first to satisfy FK).
    prior_ids = (
        (await session.execute(select(BillingEvent.id).where(BillingEvent.org_id == org_id)))
        .scalars()
        .all()
    )
    if prior_ids:
        await session.execute(
            delete(LlmBillingEvent).where(LlmBillingEvent.billing_event_id.in_(prior_ids))
        )
        await session.execute(delete(BillingEvent).where(BillingEvent.org_id == org_id))
        await session.commit()
    await _ensure_org(session, org_id)
    # Track which parents we've ensured to keep seeding fast.
    seen_ws: set[str] = set()
    seen_users: set[str] = set()
    seen_conv: set[str] = set()
    for r in rows:
        ws_id = str(r["workspace_id"])
        user_id = str(r["user_id"])
        conv_id = str(r.get("conversation_id", f"conv-seed-{org_id[:8]}"))
        if ws_id not in seen_ws:
            await _ensure_workspace(session, ws_id=ws_id, org_id=org_id)
            seen_ws.add(ws_id)
        if user_id not in seen_users:
            await _ensure_user(session, user_id=user_id)
            seen_users.add(user_id)
        if conv_id not in seen_conv:
            await _ensure_conversation(
                session,
                conv_id=conv_id,
                org_id=org_id,
                ws_id=ws_id,
                user_id=user_id,
            )
            seen_conv.add(conv_id)
        be = BillingEvent(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            conversation_id=conv_id,
            event_type="llm_call",
            cost_amount_micro=int(r["cost_micro"]),
            currency="USD",
            started_at=r["started_at"],
            ended_at=r["started_at"] + timedelta(milliseconds=200),
            duration_ms=200,
            status="ok",
        )
        le = LlmBillingEvent(
            billing_event_id=be.id,
            provider=str(r["provider"]),
            model_id=str(r["model_id"]),
            input_tokens=int(r.get("input", 0)),
            output_tokens=int(r.get("output", 0)),
            cache_read_tokens=int(r.get("cache_read", 0)),
            cache_write_tokens=int(r.get("cache_write", 0)),
        )
        session.add(be)
        session.add(le)
    await session.commit()


async def test_get_timeseries_workspace_two_workspaces_two_days() -> None:
    """Two workspaces x two days produces 2 series x 2 points each, zero-padded."""
    async with _db_session() as session:
        org = "org-ts-1"
        day1 = datetime(2026, 5, 1, 12, tzinfo=UTC)
        day2 = datetime(2026, 5, 2, 12, tzinfo=UTC)
        await _seed_events(
            session,
            org_id=org,
            rows=[
                {
                    "workspace_id": "ws-ts1-a",
                    "user_id": "usr-ts1-u1",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day1,
                    "cost_micro": 1_000_000,
                    "input": 100,
                    "output": 20,
                },
                {
                    "workspace_id": "ws-ts1-b",
                    "user_id": "usr-ts1-u2",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day1,
                    "cost_micro": 500_000,
                    "input": 50,
                    "output": 10,
                },
                {
                    "workspace_id": "ws-ts1-a",
                    "user_id": "usr-ts1-u1",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day2,
                    "cost_micro": 2_000_000,
                    "input": 200,
                    "output": 40,
                },
            ],
        )
        repo = BillingRepository(session, org_id=org)
        result = await repo.get_timeseries(
            dimension="workspace",
            since=datetime(2026, 5, 1, tzinfo=UTC),
            until=datetime(2026, 5, 2, 23, 59, 59, tzinfo=UTC),
            granularity="day",
        )
        series_by_bucket = {s["bucket"]: s for s in result}
        assert set(series_by_bucket) == {"ws-ts1-a", "ws-ts1-b"}
        # ws-ts1-a has both days; ws-ts1-b only day1, but day2 zero-padded
        ws_b_points = {p["date"]: p for p in series_by_bucket["ws-ts1-b"]["points"]}
        assert ws_b_points["2026-05-02"]["cost_amount_micro"] == 0
        assert ws_b_points["2026-05-02"]["calls"] == 0
        assert ws_b_points["2026-05-01"]["cost_amount_micro"] == 500_000


async def test_get_timeseries_top_n_collapses_to_other() -> None:
    """When buckets exceed max_series, smallest collapse into '__other'."""
    async with _db_session() as session:
        org = "org-ts-2"
        day = datetime(2026, 5, 1, 12, tzinfo=UTC)
        rows: list[dict[str, Any]] = []
        for i in range(30):
            rows.append(
                {
                    "workspace_id": f"ws-ts2-{i:02d}",
                    "user_id": "usr-ts2-u",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": (30 - i) * 100,  # ws-00 highest, ws-29 lowest
                }
            )
        await _seed_events(session, org_id=org, rows=rows)
        repo = BillingRepository(session, org_id=org)
        series = await repo.get_timeseries(
            dimension="workspace",
            since=datetime(2026, 5, 1, tzinfo=UTC),
            until=datetime(2026, 5, 1, 23, 59, 59, tzinfo=UTC),
            granularity="day",
            max_series=10,
        )
        buckets = [s["bucket"] for s in series]
        assert "__other" in buckets
        assert len(series) == 10  # 9 real + 1 other
        # totals preserved
        total = sum(p["cost_amount_micro"] for s in series for p in s["points"])
        assert total == sum(r["cost_micro"] for r in rows)


async def test_cost_summary_returns_by_user(async_client: httpx.AsyncClient) -> None:
    """/admin/cost/summary returns a `by_user` aggregation with the expected fields."""
    async with _db_session() as session:
        day = datetime(2026, 5, 5, 12, tzinfo=UTC)
        await _seed_events(
            session,
            org_id=DEFAULT_ORG_ID,
            rows=[
                {
                    "workspace_id": DEFAULT_WS_ID,
                    "user_id": "usr-by-user-a",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": 1_500_000,
                    "input": 120,
                    "output": 30,
                    "cache_read": 5,
                    "cache_write": 7,
                },
                {
                    "workspace_id": DEFAULT_WS_ID,
                    "user_id": "usr-by-user-b",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": 750_000,
                    "input": 60,
                    "output": 12,
                    "cache_read": 2,
                    "cache_write": 3,
                },
            ],
        )

    resp = await async_client.get(
        "/api/v1/admin/cost/summary",
        params={"from_date": "2026-05-01", "to_date": "2026-05-31"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "by_user" in body, f"missing `by_user` key in response: {body.keys()}"
    by_user = body["by_user"]
    assert isinstance(by_user, list)
    seeded_buckets = {row["bucket"] for row in by_user}
    assert {"usr-by-user-a", "usr-by-user-b"}.issubset(seeded_buckets)

    expected_fields = {
        "bucket",
        "bucket_type",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_amount_micro",
        "currency",
        "call_count",
    }
    for row in by_user:
        assert expected_fields.issubset(row.keys()), (
            f"row missing fields: expected {expected_fields}, got {row.keys()}"
        )
        assert row["bucket_type"] == "user"


async def test_timeseries_workspace_happy_path(async_client: httpx.AsyncClient) -> None:
    """Seed two workspaces, verify both appear unfiltered and only one with workspace_ids."""
    ws_a = "ws-ts-hp-a"
    ws_b = "ws-ts-hp-b"
    async with _db_session() as session:
        day = datetime(2026, 5, 5, 12, tzinfo=UTC)
        await _seed_events(
            session,
            org_id=DEFAULT_ORG_ID,
            rows=[
                {
                    "workspace_id": ws_a,
                    "user_id": "usr-ts-hp-a",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": 1_000_000,
                    "input": 100,
                    "output": 20,
                },
                {
                    "workspace_id": ws_b,
                    "user_id": "usr-ts-hp-b",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": 500_000,
                    "input": 50,
                    "output": 10,
                },
            ],
        )

    # The default range is current-month-to-date; the seeded events live in
    # 2026-05, so pin the query range explicitly to cover the seed date.
    range_params = {"from_date": "2026-05-01", "to_date": "2026-05-31"}

    # 1. Unfiltered: both workspace buckets present
    resp = await async_client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "workspace", "granularity": "day", **range_params},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dimension"] == "workspace"
    assert body["granularity"] == "day"
    assert "series" in body and isinstance(body["series"], list)
    buckets = {s["bucket"] for s in body["series"]}
    assert {ws_a, ws_b}.issubset(buckets), f"expected both ws buckets, got {buckets}"

    # 2. Filtered by workspace_ids=ws_a: only ws_a bucket appears
    resp = await async_client.get(
        "/api/v1/admin/cost/timeseries",
        params={
            "dimension": "workspace",
            "granularity": "day",
            "workspace_ids": ws_a,
            **range_params,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    filtered_buckets = {s["bucket"] for s in body["series"]}
    assert filtered_buckets == {ws_a}, (
        f"expected only {ws_a} bucket after filter, got {filtered_buckets}"
    )


async def test_timeseries_rejects_invalid_dimension(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "skill"},
    )
    assert resp.status_code == 422  # FastAPI validation


async def test_timeseries_requires_admin(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Non-admin org member is rejected by the admin gate."""
    client, _ws_id = member_client
    resp = await client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "workspace"},
    )
    assert resp.status_code == 403, resp.text


async def test_get_timeseries_weekly_granularity_aggregates_days() -> None:
    """Two events 3 days apart in the same week land in a single weekly bucket."""
    async with _db_session() as session:
        org = "org-tsweek-1"
        # Pick a Wednesday and Saturday in 2026-05 — both in the same Monday-anchored week
        d1 = datetime(2026, 5, 6, 12, tzinfo=UTC)  # Wed
        d2 = datetime(2026, 5, 9, 12, tzinfo=UTC)  # Sat
        await _seed_events(
            session,
            org_id=org,
            rows=[
                {
                    "workspace_id": "ws-w-a",
                    "user_id": "usr-w-1",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": d1,
                    "cost_micro": 1_000_000,
                    "input": 100,
                    "output": 20,
                    "conversation_id": "conv-w-a",
                },
                {
                    "workspace_id": "ws-w-a",
                    "user_id": "usr-w-1",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": d2,
                    "cost_micro": 2_000_000,
                    "input": 200,
                    "output": 40,
                    "conversation_id": "conv-w-b",
                },
            ],
        )
        repo = BillingRepository(session, org_id=org)
        result = await repo.get_timeseries(
            dimension="workspace",
            since=datetime(2026, 5, 4, tzinfo=UTC),  # Mon
            until=datetime(2026, 5, 10, 23, 59, 59, tzinfo=UTC),  # Sun
            granularity="week",
        )
        assert len(result) == 1
        series = result[0]
        assert series["bucket"] == "ws-w-a"
        # Both events collapse into one point
        nonzero = [p for p in series["points"] if p["cost_amount_micro"] > 0]
        assert len(nonzero) == 1
        assert nonzero[0]["cost_amount_micro"] == 3_000_000
        assert nonzero[0]["input_tokens"] == 300
