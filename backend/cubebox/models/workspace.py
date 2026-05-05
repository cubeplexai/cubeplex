"""Workspace model — collaboration unit inside an Organization."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from cubebox.models.public_id import PREFIX_WORKSPACE, generate_public_id


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"
    __table_args__ = (Index("ix_workspaces_org", "org_id"),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_WORKSPACE),
        primary_key=True,
        max_length=20,
    )
    org_id: str = Field(foreign_key="organizations.id", max_length=20)
    name: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
