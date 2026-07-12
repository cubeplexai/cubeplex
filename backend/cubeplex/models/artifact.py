"""Artifact model."""

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin
from cubeplex.utils.time import utc_isoformat


class Artifact(CubeplexBase, OrgScopedMixin, table=True):
    """Artifact model for agent-generated deliverables."""

    _PREFIX: ClassVar[str] = "art"
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_org_ws", "org_id", "workspace_id"),)

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)
    name: str = Field(max_length=255)
    artifact_type: str = Field(max_length=50)
    path: str = Field(max_length=1024)
    entry_file: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    version: int = Field(default=1)

    def to_dict(self) -> dict[str, object]:
        """Convert to API-friendly dict."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "name": self.name,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "entry_file": self.entry_file,
            "mime_type": self.mime_type,
            "description": self.description,
            "version": self.version,
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
        }
