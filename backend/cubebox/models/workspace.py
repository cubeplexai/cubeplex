"""Workspace model — collaboration unit inside an Organization."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"
    __table_args__ = (Index("ix_workspaces_org", "org_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    org_id: str = Field(max_length=32)
    name: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
