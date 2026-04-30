"""OrgProviderOverride — sparse per-org enabled/disabled for system providers."""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class OrgProviderOverride(SQLModel, table=True):
    __tablename__ = "org_provider_overrides"
    __table_args__ = (UniqueConstraint("org_id", "provider_id", name="uq_org_provider_override"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    provider_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
