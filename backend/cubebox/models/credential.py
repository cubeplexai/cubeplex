"""Credential vault models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Credential(SQLModel, table=True):
    """Vault entry. v1 only kind='mcp_server'; future kinds extend without schema changes."""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("org_id", "kind", "name", name="uq_credential_org_kind_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    kind: str = Field(max_length=32)
    name: str = Field(max_length=128)
    value_encrypted: bytes
    cred_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
