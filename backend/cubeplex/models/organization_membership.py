"""OrganizationMembership — User × Organization × org-level role.

Distinct from workspace `Membership`. One owner per org enforced by a
partial unique index. Workspace-level admin status is orthogonal to org role.
"""

from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from cubeplex.models.mixins import TimestampMixin


class OrgRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class OrganizationMembership(SQLModel, TimestampMixin, table=True):
    """Links a User to an Organization with an OrgRole; composite PK."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="organization_memberships_user_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="organization_memberships_org_id_fkey",
            ondelete="CASCADE",
        ),
        Index("ix_org_memberships_org_id", "org_id"),
        Index(
            "uq_org_membership_owner",
            "org_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    user_id: str = Field(primary_key=True, max_length=20)
    org_id: str = Field(primary_key=True, max_length=20)
    role: str = Field(max_length=32)  # values from OrgRole enum
