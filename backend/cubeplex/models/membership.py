"""Membership model — N:M between User and Workspace, with role."""

from enum import StrEnum

from sqlmodel import Field, SQLModel

from cubeplex.models.mixins import TimestampMixin


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class Membership(SQLModel, TimestampMixin, table=True):
    """Links a User to a Workspace with a Role; composite PK."""

    __tablename__ = "memberships"

    user_id: str = Field(primary_key=True, foreign_key="users.id", max_length=20)
    workspace_id: str = Field(primary_key=True, foreign_key="workspaces.id", max_length=20)
    role: str = Field(max_length=32)  # values from Role enum
