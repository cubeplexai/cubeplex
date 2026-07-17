"""Org-scoped invite token — single-use, time-limited org invitation.

Coexists with the workspace-scoped InviteToken. Accepting an org invite
grants an OrganizationMembership (ADMIN or MEMBER only — never OWNER).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.public_id import PREFIX_ORG_INVITE, generate_public_id


def _default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


class OrgInviteToken(SQLModel, table=True):
    __tablename__ = "org_invite_tokens"
    __table_args__ = (Index("ix_org_invite_tokens_expires", "expires_at"),)

    id: str = Field(
        primary_key=True,
        max_length=20,
        default_factory=lambda: generate_public_id(PREFIX_ORG_INVITE),
    )
    token: str = Field(
        default_factory=lambda: str(uuid7()),
        max_length=64,
        index=True,
        unique=True,
    )
    org_id: str = Field(foreign_key="organizations.id", max_length=20)
    role: str = Field(max_length=32)  # OrgRole value: "admin" | "member"
    created_by: str = Field(foreign_key="users.id", max_length=20)
    expires_at: datetime = Field(
        default_factory=_default_expiry,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
