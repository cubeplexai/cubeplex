"""Durable source identity; retrying an input never grants it a new generation."""

from enum import StrEnum
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin, org_scope_index
from cubeplex.models.public_id import PREFIX_CONVERSATION_EXECUTION_ADMISSION


class ExecutionSourceKind(StrEnum):
    user_message = "user_message"
    schedule_occurrence = "schedule_occurrence"
    trigger_occurrence = "trigger_occurrence"
    background_task = "background_task"


class ConversationExecutionAdmission(CubeplexBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_CONVERSATION_EXECUTION_ADMISSION
    __tablename__ = "conversation_execution_admissions"
    __table_args__ = (
        org_scope_index("conversation_execution_admissions"),
        UniqueConstraint(
            "org_id", "workspace_id", "source_kind", "source_id", name="uq_execution_source"
        ),
        Index("ix_execution_admissions_conv_gen", "conversation_id", "execution_generation"),
        CheckConstraint("execution_generation >= 0", name="ck_execution_admission_generation"),
    )

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    actor_user_id: str = Field(foreign_key="users.id", max_length=20)
    source_kind: str = Field(max_length=32)
    source_id: str = Field(max_length=255)
    execution_generation: int = Field(sa_column=Column(BigInteger, nullable=False))
    run_id: str | None = Field(default=None, max_length=64)
