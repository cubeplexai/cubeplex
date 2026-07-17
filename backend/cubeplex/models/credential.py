"""Credential vault models."""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class Credential(CubeplexBase, table=True):
    """Vault entry. Kinds: 'mcp_server', 'provider_api_key'.

    System-level credentials (e.g. seeded provider api keys) have org_id=NULL;
    org-scoped credentials have org_id set. Uniqueness is enforced separately
    for each scope via partial unique indexes.
    """

    _PREFIX: ClassVar[str] = "cred"
    __tablename__ = "credentials"
    __table_args__ = (
        Index(
            "uq_credential_org_kind_name",
            "org_id",
            "kind",
            "name",
            unique=True,
            postgresql_where="org_id IS NOT NULL",
        ),
        Index(
            "uq_credential_system_kind_name",
            "kind",
            "name",
            unique=True,
            postgresql_where="org_id IS NULL",
        ),
    )

    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True, nullable=True
    )
    kind: str = Field(max_length=32)
    name: str = Field(max_length=128)
    value_encrypted: bytes
    cred_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
