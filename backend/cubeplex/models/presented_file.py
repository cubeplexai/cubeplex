"""PresentedFile — agent-shown sandbox file durable for conversation history."""

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin
from cubeplex.utils.time import utc_isoformat


class PresentedFile(CubeplexBase, OrgScopedMixin, table=True):
    """A file the agent presented from the sandbox into the conversation.

    ObjectStore is the source of truth after present. Lifecycle matches the
    conversation (soft-delete hides; hard GC removes rows + objects).
    Not an artifact — does not appear in the gallery.
    """

    _PREFIX: ClassVar[str] = "pfile"
    __tablename__ = "presented_files"
    __table_args__ = (Index("ix_presented_files_org_ws", "org_id", "workspace_id"),)

    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)
    run_id: str | None = Field(default=None, max_length=64, nullable=True)

    source_path: str = Field(max_length=1024)
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=128)
    size_bytes: int
    kind: str = Field(max_length=16)

    object_key: str = Field(max_length=1024)
    thumbnail_object_key: str | None = Field(default=None, max_length=1024)

    width: int | None = None
    height: int | None = None
    caption: str | None = Field(default=None, max_length=1024)

    def to_dict(self) -> dict[str, object]:
        """API / tool-result shape."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "source_path": self.source_path,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "caption": self.caption,
            "width": self.width,
            "height": self.height,
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
        }
