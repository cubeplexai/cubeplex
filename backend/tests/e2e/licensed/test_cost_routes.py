"""E2E: the cost reporting HTTP surface, which only exists when the optional
package is installed. Skipped on a default install; a companion module in
tests/e2e/ asserts the routes are absent there.
"""

from datetime import UTC, datetime

import httpx
import pytest

pytest.importorskip("cubeplex_ee", reason="cost reporting lives in the optional package")

from tests.e2e.billing_fixtures import (  # noqa: E402  -- must follow importorskip
    _db_session,
    _seed_events,
)
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID  # noqa: E402

pytestmark = pytest.mark.e2e

COST_BASE = "/api/v1/admin/_extensions/cubeplex_ee/cost"


async def test_cost_summary_returns_by_user(async_client: httpx.AsyncClient) -> None:
    """The summary endpoint returns a `by_user` aggregation with the expected fields."""
    async with _db_session() as session:
        day = datetime(2026, 5, 5, 12, tzinfo=UTC)
        await _seed_events(
            session,
            org_id=DEFAULT_ORG_ID,
            rows=[
                {
                    "workspace_id": DEFAULT_WS_ID,
                    "user_id": "usr-by-user-a",
                    "user_display_name": "Alice Admin",
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
                    # No display_name → label falls back to email.
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
        f"{COST_BASE}/summary",
        params={"from_date": "2026-05-01", "to_date": "2026-05-31"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "by_user" in body, f"missing `by_user` key in response: {body.keys()}"
    by_user = body["by_user"]
    assert isinstance(by_user, list)
    by_bucket = {row["bucket"]: row for row in by_user}
    assert {"usr-by-user-a", "usr-by-user-b"}.issubset(by_bucket)

    expected_fields = {
        "bucket",
        "bucket_type",
        "bucket_label",
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

    assert by_bucket["usr-by-user-a"]["bucket_label"] == "Alice Admin"
    assert by_bucket["usr-by-user-b"]["bucket_label"] == "usr-by-user-b@test-billing-cost.local"

    # Workspace label resolves from workspace.name (not the raw public id).
    by_ws = {row["bucket"]: row for row in body["by_workspace"]}
    assert DEFAULT_WS_ID in by_ws
    ws_label = by_ws[DEFAULT_WS_ID]["bucket_label"]
    assert ws_label is not None and ws_label != DEFAULT_WS_ID


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
        f"{COST_BASE}/timeseries",
        params={"dimension": "workspace", "granularity": "day", **range_params},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dimension"] == "workspace"
    assert body["granularity"] == "day"
    assert "series" in body and isinstance(body["series"], list)
    by_bucket = {s["bucket"]: s for s in body["series"]}
    assert {ws_a, ws_b}.issubset(by_bucket), f"expected both ws buckets, got {set(by_bucket)}"
    assert by_bucket[ws_a]["bucket_label"] == f"Test {ws_a}"
    assert by_bucket[ws_b]["bucket_label"] == f"Test {ws_b}"

    # 2. Filtered by workspace_ids=ws_a: only ws_a bucket appears
    resp = await async_client.get(
        f"{COST_BASE}/timeseries",
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
        f"{COST_BASE}/timeseries",
        params={"dimension": "skill"},
    )
    assert resp.status_code == 422  # FastAPI validation


async def test_timeseries_requires_admin(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Non-admin org member is rejected by the admin gate."""
    client, _ws_id = member_client
    resp = await client.get(
        f"{COST_BASE}/timeseries",
        params={"dimension": "workspace"},
    )
    assert resp.status_code == 403, resp.text
