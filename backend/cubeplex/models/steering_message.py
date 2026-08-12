"""Durable steering accepted while a conversation is paused for HITL."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin, org_scope_index
from cubeplex.models.public_id import PREFIX_STEERING_MESSAGE


class SteeringMessageState(StrEnum):
    queued = "queued"
    dispatched = "dispatched"
    cancel_requested = "cancel_requested"
    injected = "injected"
    cancelled = "cancelled"
    failed = "failed"


class SteeringMessage(CubeplexBase, OrgScopedMixin, table=True):
    """Postgres source of truth for steering spanning a durable HITL pause."""

    _PREFIX: ClassVar[str] = PREFIX_STEERING_MESSAGE
    __tablename__ = "steering_messages"
    __table_args__ = (
        org_scope_index("steering_messages"),
        Index(
            "uq_steering_messages_conversation_client_id",
            "conversation_id",
            "client_steer_id",
            unique=True,
        ),
        Index(
            "ix_steering_messages_run_delivery",
            "run_id",
            "state",
            "created_at",
            "id",
        ),
    )

    conversation_id: str = Field(
        foreign_key="conversations.id",
        max_length=20,
        ondelete="CASCADE",
    )
    run_id: str = Field(max_length=64)
    client_steer_id: str = Field(max_length=64)
    content: str = Field(sa_column=Column(Text, nullable=False))
    sender_user_id: str = Field(foreign_key="users.id", max_length=20)
    sender_display_name: str | None = Field(default=None, max_length=255)
    hitl_question_id: str = Field(max_length=128)
    state: SteeringMessageState = Field(
        default=SteeringMessageState.queued,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default=SteeringMessageState.queued.value,
        ),
    )
    delivery_owner: str | None = Field(default=None, max_length=128)
    delivery_lease_until: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
