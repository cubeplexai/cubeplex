"""BillingRepository — insert and query billing_events + billing_llm_events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.billing import BillingEvent, LlmBillingEvent


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

    async def stream_events_for_export(
        self,
        *,
        since: datetime,
        until: datetime,
        workspace_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
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
