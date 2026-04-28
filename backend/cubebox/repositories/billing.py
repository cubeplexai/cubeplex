"""BillingRepository — insert and query billing_events + billing_llm_events."""

from __future__ import annotations

from datetime import datetime

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
