"""E2E: BillingRepository timeseries aggregation. Core surface, default install."""

from datetime import UTC, datetime
from typing import Any

import pytest

from cubeplex.repositories import BillingRepository
from tests.e2e.billing_fixtures import _db_session, _seed_events

pytestmark = pytest.mark.e2e


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


async def test_get_timeseries_rank_by_tokens_with_zero_cost() -> None:
    """When all costs are 0, rank_by=tokens keeps highest-token buckets outside __other."""
    async with _db_session() as session:
        org = "org-ts-rank-tokens"
        day = datetime(2026, 5, 2, 12, tzinfo=UTC)
        rows: list[dict[str, Any]] = []
        for i in range(12):
            # All zero cost; tokens decrease with i so ws-00 is highest tokens.
            rows.append(
                {
                    "workspace_id": f"ws-tok-{i:02d}",
                    "user_id": "usr-tok-u",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "started_at": day,
                    "cost_micro": 0,
                    "input": (12 - i) * 1000,
                    "output": 100,
                }
            )
        await _seed_events(session, org_id=org, rows=rows)
        repo = BillingRepository(session, org_id=org)
        since = datetime(2026, 5, 2, tzinfo=UTC)
        until = datetime(2026, 5, 2, 23, 59, 59, tzinfo=UTC)
        series = await repo.get_timeseries(
            dimension="workspace",
            since=since,
            until=until,
            granularity="day",
            max_series=5,
            rank_by="tokens",
        )
        buckets = [s["bucket"] for s in series]
        assert "__other" in buckets
        assert len(series) == 5  # 4 real + __other
        real = [b for b in buckets if b != "__other"]
        assert "ws-tok-00" in real
        assert "ws-tok-01" in real
        assert "ws-tok-11" not in real  # lowest tokens collapsed
        total_tokens = sum(
            p["input_tokens"] + p["output_tokens"] for s in series for p in s["points"]
        )
        assert total_tokens == sum(int(r["input"]) + int(r["output"]) for r in rows)


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
