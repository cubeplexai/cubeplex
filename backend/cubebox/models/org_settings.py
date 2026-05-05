"""OrgSettings — per-org key-value settings for LLM defaults."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class OrgSettings(SQLModel, table=True):
    __tablename__ = "org_settings"

    org_id: str = Field(primary_key=True, foreign_key="organizations.id", max_length=20)
    key: str = Field(primary_key=True, max_length=64)
    value: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
