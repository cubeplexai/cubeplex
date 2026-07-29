"""Pydantic schemas for billing/cost API responses."""

from datetime import date
from typing import Literal

from pydantic import BaseModel


class CostAggregateRow(BaseModel):
    bucket: str  # workspace_id | user_id | "provider/model_id" | "YYYY-MM-DD"
    bucket_type: str  # "workspace" | "user" | "model" | "day"
    # Human-readable label for workspace/user buckets; None for model/day or
    # when the entity no longer exists (clients should fall back to `bucket`).
    bucket_label: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_amount_micro: int  # amount × 10⁶; divide by 1_000_000 for display
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
    by_user: list[CostAggregateRow]
    by_day: list[CostAggregateRow]


class TimeseriesPoint(BaseModel):
    date: str  # YYYY-MM-DD (or week-start date if granularity=week)
    cost_amount_micro: int
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class TimeseriesSeries(BaseModel):
    bucket: str  # workspace_id | user_id | "provider/model_id" | "__other"
    # Human-readable label for workspace/user series; None for model/__other or
    # when the entity no longer exists (clients should fall back to `bucket`).
    bucket_label: str | None = None
    points: list[TimeseriesPoint]
    currency: str


class TimeseriesResponse(BaseModel):
    from_date: date
    to_date: date
    granularity: Literal["day", "week"]
    dimension: Literal["workspace", "model", "user"]
    series: list[TimeseriesSeries]
    currency: str
