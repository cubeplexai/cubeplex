"""API key model — personal access tokens for headless cubeplex API access.

A key acts as its owning user: the same workspace memberships, the same role,
the same RBAC. The plaintext token is shown to the user exactly once on
creation; the database stores only ``sha256(token)`` so a leaked DB row
cannot be replayed. Revocation = delete the row.
"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class ApiKey(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = "ak"
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        Index("uq_api_keys_hashed_key", "hashed_key", unique=True),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20)
    label: str = Field(max_length=100)
    # First ~12 chars of the plaintext token, shown in list views so users
    # can identify a key without revealing the secret.
    prefix: str = Field(max_length=16)
    # sha256 hex digest of the full plaintext token. 64 chars.
    hashed_key: str = Field(max_length=64)
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
