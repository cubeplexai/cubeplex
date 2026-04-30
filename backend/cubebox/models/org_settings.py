"""OrgSettings — per-org key-value settings for LLM defaults."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class OrgSettings(SQLModel, table=True):
    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_settings_org_key"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    key: str = Field(max_length=64)
    value: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
