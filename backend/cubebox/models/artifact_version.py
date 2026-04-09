"""ArtifactVersion model — tracks version history for artifacts."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class ArtifactVersion(SQLModel, table=True):
    """Snapshot of artifact metadata at a specific version."""

    __tablename__ = "artifact_versions"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    artifact_id: str = Field(foreign_key="artifacts.id", index=True)
    version: int
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    path: str = Field(max_length=1024)
    entry_file: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        """Convert to API-friendly dict."""
        return {
            "id": self.id,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "entry_file": self.entry_file,
            "mime_type": self.mime_type,
            "created_at": self.created_at.isoformat(),
        }
