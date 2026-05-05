"""Billing models — parent/child tables for cost tracking."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import OrgScopedMixin
from cubebox.models.public_id import (
    PREFIX_BILLING_EVENT,
    PREFIX_BILLING_LLM_EVENT,
    generate_public_id,
)


class BillingEvent(SQLModel, OrgScopedMixin, table=True):
    """Parent billing row — one per billable event (LLM call, sandbox, storage…)."""

    __tablename__ = "billing_events"
    __table_args__ = (
        Index("ix_billing_events_org_ws_time", "org_id", "workspace_id", "started_at"),
        Index("ix_billing_events_org_time", "org_id", "started_at"),
        Index("ix_billing_events_conversation", "conversation_id"),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_BILLING_EVENT),
        primary_key=True,
        max_length=20,
    )
    user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    event_type: str = Field(max_length=32)  # "llm_call" | "sandbox_compute" | …
    cost_amount_micro: int = Field(default=0)  # amount × 10⁶ in `currency`
    currency: str = Field(default="USD", max_length=3)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0)
    status: str = Field(max_length=20)  # "success" | "error" | "fallback_failed"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LlmBillingEvent(SQLModel, table=True):
    """Child row for LLM-specific fields (JOINed with BillingEvent)."""

    __tablename__ = "billing_llm_events"
    __table_args__ = (
        Index("ix_billing_llm_events_provider_model", "provider", "model_id"),
        Index("ix_billing_llm_events_parent_run_id", "parent_run_id"),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_BILLING_LLM_EVENT),
        primary_key=True,
        max_length=20,
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
    )  # LangSmith external UUID, not our DB row
    subagent_depth: int = Field(default=0)
    error_class: str | None = Field(default=None, max_length=128)
