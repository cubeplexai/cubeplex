"""Backfill cursor — lets the script resume after interruption."""

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_BACKFILL, generate_public_id


class SearchBackfillProgress(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_BACKFILL
    __tablename__ = "search_backfill_progress"
    __table_args__ = (Index("ix_sbp_ws", "workspace_id", unique=True),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_BACKFILL),
        primary_key=True,
        max_length=20,
    )
    last_conversation_id: str | None = Field(default=None, max_length=20)
    enqueued_count: int = Field(default=0)
    done: bool = Field(default=False)
