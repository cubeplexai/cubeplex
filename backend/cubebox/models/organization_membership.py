"""OrganizationMembership — User × Organization × org-level role.

Distinct from workspace `Membership`. One owner per org enforced by a
partial unique index (see alembic migration). Workspace-level admin
status is orthogonal to org role.
"""

from enum import StrEnum

from sqlmodel import Field, SQLModel

from cubebox.models.mixins import TimestampMixin


class OrgRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class OrganizationMembership(SQLModel, TimestampMixin, table=True):
    """Links a User to an Organization with an OrgRole; composite PK."""

    __tablename__ = "organization_memberships"

    user_id: str = Field(primary_key=True, foreign_key="users.id", max_length=20)
    org_id: str = Field(primary_key=True, foreign_key="organizations.id", max_length=20)
    role: str = Field(max_length=32)  # values from OrgRole enum
