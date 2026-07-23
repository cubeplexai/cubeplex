"""BillingRepository — insert and query billing_events + billing_llm_events."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.billing import BillingEvent, LlmBillingEvent


class BillingRepository:
    """Handles all reads and writes for the billing tables.

    org_id is required at construction; workspace_id is passed per-query
    so the same repo instance can serve both workspace-scoped and org-wide queries.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def insert_llm_event(self, billing_evt: BillingEvent, llm_evt: LlmBillingEvent) -> None:
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
            select(  # type: ignore[call-overload]
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),  # type: ignore[arg-type]
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
            bucket_col = func.date(BillingEvent.started_at).label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                func.date(BillingEvent.started_at), BillingEvent.currency
            )
        elif group_by == "user":
            bucket_col = BillingEvent.user_id.label("bucket")  # type: ignore[attr-defined]
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.user_id, BillingEvent.currency
            )
        else:  # model
            bucket_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(  # type: ignore[attr-defined]
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
            select(  # type: ignore[call-overload]
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),  # type: ignore[arg-type]
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
            bucket_col = BillingEvent.workspace_id.label("bucket")  # type: ignore[attr-defined]
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.workspace_id, BillingEvent.currency
            )
        elif group_by == "user":
            bucket_col = BillingEvent.user_id.label("bucket")  # type: ignore[attr-defined]
            stmt = base.add_columns(bucket_col).group_by(
                BillingEvent.user_id, BillingEvent.currency
            )
        elif group_by == "day":
            bucket_col = func.date(BillingEvent.started_at).label("bucket")
            stmt = base.add_columns(bucket_col).group_by(
                func.date(BillingEvent.started_at), BillingEvent.currency
            )
        else:  # model
            bucket_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(  # type: ignore[attr-defined]
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

    async def get_timeseries(
        self,
        *,
        dimension: Literal["workspace", "user", "model"],
        since: datetime,
        until: datetime,
        granularity: Literal["day", "week"] = "day",
        workspace_ids: list[str] | None = None,
        models: list[str] | None = None,
        max_series: int = 25,
        rank_by: Literal["cost", "tokens"] = "cost",
    ) -> list[dict[str, Any]]:
        """2D aggregation: (dimension bucket x time bucket).

        The total number of returned series never exceeds ``max_series``. If the
        underlying data has more than ``max_series`` distinct buckets, the
        lowest-ranked ones are collapsed into a single series with
        ``bucket="__other"`` so that the total length is ``max_series`` (when
        more buckets than the cap exist) or ``len(distinct buckets)`` (when
        fewer). Ranking uses cost (default) or token totals
        (``input_tokens + output_tokens``) when ``rank_by="tokens"``.
        """
        # Time bucket column
        if granularity == "week":
            time_col = func.date_trunc("week", BillingEvent.started_at).label("time_bucket")
        else:
            time_col = func.date(BillingEvent.started_at).label("time_bucket")

        # Dimension bucket column
        dim_group: Any
        if dimension == "workspace":
            dim_col = BillingEvent.workspace_id.label("bucket")  # type: ignore[attr-defined]
            dim_group = BillingEvent.workspace_id
        elif dimension == "user":
            dim_col = BillingEvent.user_id.label("bucket")  # type: ignore[attr-defined]
            dim_group = BillingEvent.user_id
        else:  # model
            dim_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(  # type: ignore[attr-defined]
                "bucket"
            )
            dim_group = (LlmBillingEvent.provider, LlmBillingEvent.model_id)

        stmt = (
            select(  # type: ignore[call-overload]
                dim_col,
                time_col,
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),  # type: ignore[arg-type]
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
        if workspace_ids:
            stmt = stmt.where(BillingEvent.workspace_id.in_(workspace_ids))  # type: ignore[attr-defined]
        if models:
            # models arrive as "provider/model_id" strings; rebuild predicate
            conds: list[Any] = []
            for m in models:
                if "/" in m:
                    p, mid = m.split("/", 1)
                    conds.append(
                        and_(
                            LlmBillingEvent.provider == p,  # type: ignore[arg-type]
                            LlmBillingEvent.model_id == mid,  # type: ignore[arg-type]
                        )
                    )
            if conds:
                stmt = stmt.where(or_(*conds))

        if isinstance(dim_group, tuple):
            stmt = stmt.group_by(*dim_group, time_col, BillingEvent.currency)
        else:
            stmt = stmt.group_by(dim_group, time_col, BillingEvent.currency)

        rows = (await self.session.execute(stmt)).all()

        # Build series map: bucket -> {date: point}
        series_map: dict[str, dict[str, dict[str, Any]]] = {}
        bucket_totals: dict[str, int] = {}
        series_currency: dict[str, str] = {}
        for r in rows:
            bucket = str(r.bucket)
            # date_trunc('week', ...) returns a timestamp (datetime); func.date(...)
            # returns a date. Normalize both to a YYYY-MM-DD string so the key
            # matches the date_axis used for zero-padding below.
            tb = r.time_bucket
            if hasattr(tb, "date"):
                date_str = tb.date().isoformat()
            elif hasattr(tb, "isoformat"):
                date_str = tb.isoformat()
            else:
                date_str = str(tb)
            input_tok = int(r.input_tokens or 0)
            output_tok = int(r.output_tokens or 0)
            cost_micro = int(r.cost or 0)
            series_map.setdefault(bucket, {})[date_str] = {
                "date": date_str,
                "cost_amount_micro": cost_micro,
                "calls": int(r.calls or 0),
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "cache_read_tokens": int(r.cache_read_tokens or 0),
                "cache_write_tokens": int(r.cache_write_tokens or 0),
            }
            rank_delta = cost_micro if rank_by == "cost" else input_tok + output_tok
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + rank_delta
            # First currency seen for a bucket wins; multi-currency per bucket is
            # out of scope (spec assumes a single currency per org).
            series_currency.setdefault(bucket, r.currency)

        # Build full date axis (zero-pad)
        step = timedelta(days=7) if granularity == "week" else timedelta(days=1)
        cur = (
            since.date()
            if granularity == "day"
            else (since.date() - timedelta(days=since.weekday()))
        )
        end = until.date()
        date_axis: list[str] = []
        while cur <= end:
            date_axis.append(cur.isoformat())
            cur = cur + step

        def _zero_point(d: str) -> dict[str, Any]:
            return {
                "date": d,
                "cost_amount_micro": 0,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }

        # Top-N + "__other"
        ranked = sorted(bucket_totals.items(), key=lambda kv: kv[1], reverse=True)
        keep = {b for b, _ in ranked[: max(0, max_series - 1)]}
        other_points: dict[str, dict[str, Any]] = {}
        other_currency: str | None = None
        result_series: list[dict[str, Any]] = []
        sum_keys = (
            "cost_amount_micro",
            "calls",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
        for bucket, by_date in series_map.items():
            bucket_points: list[dict[str, Any]] = [
                by_date.get(d, _zero_point(d)) for d in date_axis
            ]
            if bucket in keep or len(ranked) <= max_series:
                result_series.append(
                    {
                        "bucket": bucket,
                        "points": bucket_points,
                        "currency": series_currency.get(bucket, "USD"),
                    }
                )
            else:
                if other_currency is None:
                    other_currency = series_currency.get(bucket)
                for bp in bucket_points:
                    bp_typed: dict[str, Any] = bp
                    date_key: str = str(bp_typed["date"])
                    op = other_points.setdefault(date_key, _zero_point(date_key))
                    for k in sum_keys:
                        op[k] = int(op[k]) + int(bp_typed[k])
        if other_points:
            result_series.append(
                {
                    "bucket": "__other",
                    "points": [other_points.get(d, _zero_point(d)) for d in date_axis],
                    "currency": other_currency or "USD",
                }
            )
        return result_series

    async def stream_events_for_export(
        self,
        *,
        since: datetime,
        until: datetime,
        workspace_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream flat join rows for CSV export (lazy cursor, no full load)."""
        stmt = (
            select(  # type: ignore[call-overload]
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
