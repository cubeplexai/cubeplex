"""OrgProviderOverride — sparse per-org enabled/disabled for system providers."""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from cubebox.models.public_id import PREFIX_ORG_PROVIDER_OVERRIDE, generate_public_id


class OrgProviderOverride(SQLModel, table=True):
    __tablename__ = "org_provider_overrides"
    __table_args__ = (UniqueConstraint("org_id", "provider_id", name="uq_org_provider_override"),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_ORG_PROVIDER_OVERRIDE),
        primary_key=True,
        max_length=20,
    )
    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    provider_id: str = Field(foreign_key="providers.id", max_length=20, index=True)
    enabled: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
