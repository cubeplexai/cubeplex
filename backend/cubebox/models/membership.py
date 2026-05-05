"""Membership model — N:M between User and Workspace, with role."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class Membership(SQLModel, table=True):
    __tablename__ = "memberships"

    user_id: str = Field(primary_key=True, foreign_key="users.id", max_length=20)
    workspace_id: str = Field(primary_key=True, foreign_key="workspaces.id", max_length=20)
    role: str = Field(max_length=32)  # values from Role enum
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
