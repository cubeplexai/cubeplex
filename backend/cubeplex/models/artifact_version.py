"""ArtifactVersion model — tracks version history for artifacts."""

from typing import ClassVar

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin
from cubeplex.utils.time import utc_isoformat


class ArtifactVersion(CubeplexBase, OrgScopedMixin, table=True):
    """Snapshot of artifact metadata at a specific version."""

    _PREFIX: ClassVar[str] = "artv"
    __tablename__ = "artifact_versions"
    __table_args__ = (
        Index("ix_artifact_versions_org_ws", "org_id", "workspace_id"),
        UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_version"),
    )

    artifact_id: str = Field(foreign_key="artifacts.id", max_length=20, index=True)
    version: int
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    path: str = Field(max_length=1024)
    entry_file: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)

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
            "updated_at": utc_isoformat(self.updated_at),
        }
