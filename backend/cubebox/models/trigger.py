"""Trigger and TriggerEvent models."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, BigInteger, Column, DateTime, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin, org_scope_index
from cubebox.models.public_id import PREFIX_TRIGGER, PREFIX_TRIGGER_EVENT


class Trigger(CubeboxBase, OrgScopedMixin, table=True):
    """Workspace-owned trigger: source + filter + target + run identity."""

    _PREFIX: ClassVar[str] = PREFIX_TRIGGER
    __tablename__ = "triggers"
    __table_args__ = (
        org_scope_index("triggers"),
        Index("ix_triggers_im_channel", "im_account_id", "im_channel_id"),
    )

    name: str = Field(max_length=128)
    enabled: bool = Field(default=True, index=True)

    # Source
    source_type: str = Field(max_length=16)
    source_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Filter (null = match all)
    filter: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Target
    target_type: str = Field(max_length=16)
    target_ref: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Payload whitelist
    payload_fields: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Conversation policy: new_each_time | im_channel
    # — see ck_triggers_conversation_policy
    conversation_policy: str = Field(default="new_each_time", max_length=16)

    # Destination columns (used when policy != new_each_time)
    topic_id: str | None = Field(
        default=None,
        foreign_key="topics.id",
        max_length=20,
        nullable=True,
        index=True,
        ondelete="SET NULL",
    )
    im_account_id: str | None = Field(
        default=None,
        foreign_key="im_connector_accounts.id",
        max_length=20,
        nullable=True,
        ondelete="SET NULL",
    )
    im_channel_id: str | None = Field(default=None, max_length=128, nullable=True)
    im_scope_key: str | None = Field(default=None, max_length=255, nullable=True)
    im_scope_kind: str | None = Field(default=None, max_length=32, nullable=True)

    # Run identity
    run_as_user_id: str = Field(foreign_key="users.id", max_length=20)

    # Rate limiting
    max_runs_per_minute: int = Field(default=10)
    rate_limit_burst: int = Field(default=20)
    rate_limit_response: str = Field(default="429", max_length=16)

    # Secret rotation
    current_secret_cred_id: str = Field(foreign_key="credentials.id", max_length=20)
    previous_secret_cred_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20
    )
    previous_secret_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # Summary counters (BIGINT, server default 0)
    events_total: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    events_success: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    events_failed: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    events_dedup_dropped: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )


class TriggerEvent(CubeboxBase, OrgScopedMixin, table=True):
    """Audit row written for every inbound event before any processing."""

    _PREFIX: ClassVar[str] = PREFIX_TRIGGER_EVENT
    __tablename__ = "trigger_events"
    __table_args__ = (
        org_scope_index("trigger_events"),
        Index("uq_trigger_event_dedup", "trigger_id", "dedup_key", unique=True),
    )

    trigger_id: str = Field(foreign_key="triggers.id", max_length=20, index=True)
    source_type: str = Field(max_length=16)
    event_type: str | None = Field(default=None)
    dedup_key: str = Field(max_length=64)

    occurred_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # Status: accepted|duplicate|filtered_out|rate_limited|failed|dead_lettered
    status: str = Field(max_length=16)

    attempts: int = Field(default=0)
    last_error: str | None = Field(default=None)

    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    resulting_run_id: str | None = Field(default=None)
    resulting_conversation_id: str | None = Field(default=None)
