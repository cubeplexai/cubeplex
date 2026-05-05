"""ArtifactVersion model — tracks version history for artifacts."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import OrgScopedMixin
from cubebox.models.public_id import PREFIX_ARTIFACT_VERSION, generate_public_id
from cubebox.utils.time import utc_isoformat


class ArtifactVersion(SQLModel, OrgScopedMixin, table=True):
    """Snapshot of artifact metadata at a specific version."""

    __tablename__ = "artifact_versions"
    __table_args__ = (Index("ix_artifact_versions_org_ws", "org_id", "workspace_id"),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_ARTIFACT_VERSION),
        primary_key=True,
        max_length=20,
    )
    artifact_id: str = Field(foreign_key="artifacts.id", max_length=20, index=True)
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
            "created_at": utc_isoformat(self.created_at),
        }
