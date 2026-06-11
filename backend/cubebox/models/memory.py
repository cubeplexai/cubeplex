"""Memory item model — personal/workspace/org scoped knowledge."""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field

from cubebox.models.mixins import CubeboxBase
from cubebox.models.public_id import PREFIX_MEMORY
from cubebox.utils.time import utc_isoformat


class MemoryScope(StrEnum):
    PERSONAL = "personal"
    WORKSPACE = "workspace"
    ORG = "org"


class MemoryType(StrEnum):
    PREFERENCE = "preference"
    PROJECT_FACT = "project_fact"
    PROCEDURE = "procedure"
    CORRECTION = "correction"
    DECISION = "decision"
    ORG_POLICY = "org_policy"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MemorySourceType(StrEnum):
    CONVERSATION = "conversation"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"
    MANUAL = "manual"
    IMPORT = "import"
    CONSOLIDATION = "consolidation"
    REFLECTION = "reflection"


class MemoryItem(CubeboxBase, table=True):
    """Memory item. Scope determines which of org_id/workspace_id/owner_user_id is set."""

    _PREFIX: ClassVar[str] = PREFIX_MEMORY
    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_personal", "scope", "owner_user_id"),
        Index("ix_memory_workspace", "scope", "workspace_id"),
        Index("ix_memory_org", "scope", "org_id"),
        Index("ix_memory_status", "status"),
    )

    scope: MemoryScope = Field()
    org_id: str | None = Field(default=None, foreign_key="organizations.id", max_length=20)
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", max_length=20)
    owner_user_id: str | None = Field(default=None, foreign_key="users.id", max_length=20)

    type: MemoryType
    content: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    status: MemoryStatus = Field(default=MemoryStatus.ACTIVE)

    source_type: MemorySourceType = Field(default=MemorySourceType.MANUAL)
    source_conversation_id: str | None = Field(default=None, max_length=20)
    source_run_id: str | None = Field(default=None, max_length=40)
    source_artifact_id: str | None = Field(default=None, max_length=20)
    source_excerpt: str | None = Field(default=None, max_length=500)

    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
    updated_by_user_id: str | None = Field(default=None, max_length=20)

    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    item_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON().with_variant(JSONB(), "postgresql")),
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "scope": self.scope.value,
            "org_id": self.org_id,
            "workspace_id": self.workspace_id,
            "owner_user_id": self.owner_user_id,
            "type": self.type.value,
            "content": self.content,
            "confidence": self.confidence,
            "status": self.status.value,
            "source_type": self.source_type.value,
            "source_conversation_id": self.source_conversation_id,
            "source_run_id": self.source_run_id,
            "source_artifact_id": self.source_artifact_id,
            "source_excerpt": self.source_excerpt,
            "created_by_user_id": self.created_by_user_id,
            "updated_by_user_id": self.updated_by_user_id,
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
            "last_used_at": utc_isoformat(self.last_used_at) if self.last_used_at else None,
            "metadata": self.item_metadata,
        }
