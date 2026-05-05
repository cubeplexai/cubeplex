"""Credential vault models."""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase


class Credential(CubeboxBase, table=True):
    """Vault entry. v1 only kind='mcp_server'; future kinds extend without schema changes."""

    _PREFIX: ClassVar[str] = "cred"
    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("org_id", "kind", "name", name="uq_credential_org_kind_name"),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    kind: str = Field(max_length=32)
    name: str = Field(max_length=128)
    value_encrypted: bytes
    cred_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
