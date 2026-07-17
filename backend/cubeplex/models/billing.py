"""Billing models — parent/child tables for cost tracking."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin


class BillingEvent(CubeplexBase, OrgScopedMixin, table=True):
    """Parent billing row — one per billable event (LLM call, sandbox, storage…)."""

    _PREFIX: ClassVar[str] = "bill"
    __tablename__ = "billing_events"
    __table_args__ = (
        Index("ix_billing_events_org_ws_time", "org_id", "workspace_id", "started_at"),
        Index("ix_billing_events_org_time", "org_id", "started_at"),
        Index("ix_billing_events_conversation", "conversation_id"),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    event_type: str = Field(max_length=32)  # "llm_call" | "sandbox_compute" | …
    cost_amount_micro: int = Field(default=0)  # amount × 10⁶ in `currency`
    currency: str = Field(default="USD", max_length=3)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    ended_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    duration_ms: int = Field(default=0)
    status: str = Field(max_length=20)  # "success" | "error" | "fallback_failed"


class LlmBillingEvent(CubeplexBase, table=True):
    """Child row for LLM-specific fields (JOINed with BillingEvent)."""

    _PREFIX: ClassVar[str] = "llmb"
    __tablename__ = "billing_llm_events"
    __table_args__ = (
        Index("ix_billing_llm_events_provider_model", "provider", "model_id"),
        Index("ix_billing_llm_events_parent_run_id", "parent_run_id"),
    )

    billing_event_id: str = Field(foreign_key="billing_events.id", max_length=20, index=True)
    provider: str = Field(max_length=64)
    model_id: str = Field(max_length=128)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)
    cache_write_tokens: int = Field(default=0)
    price_input_per_mtok_micro: int = Field(default=0)  # snapshot at write time
    price_output_per_mtok_micro: int = Field(default=0)
    price_cache_read_per_mtok_micro: int = Field(default=0)
    price_cache_write_per_mtok_micro: int = Field(default=0)
    parent_run_id: str | None = Field(
        default=None, max_length=64
    )  # External trace UUID for parent run; populated by the runtime if available
    subagent_depth: int = Field(default=0)
    error_class: str | None = Field(default=None, max_length=128)
