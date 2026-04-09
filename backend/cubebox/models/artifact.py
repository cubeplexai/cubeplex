"""Artifact model."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubebox.utils.time import utc_isoformat


class Artifact(SQLModel, table=True):
    """Artifact model for agent-generated deliverables."""

    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    name: str = Field(max_length=255)
    artifact_type: str = Field(max_length=50)
    path: str = Field(max_length=1024)
    entry_file: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

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
