"""Workspace model — collaboration unit inside an Organization."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class Workspace(CubeplexBase, table=True):
    """Workspace belongs to an Organization; users collaborate inside a workspace."""

    _PREFIX: ClassVar[str] = "ws"
    __tablename__ = "workspaces"
    __table_args__ = (Index("ix_workspaces_org", "org_id"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20)
    name: str = Field(max_length=255)
    archived_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
