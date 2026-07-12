"""OrgProviderOverride — sparse per-org enabled/disabled for system providers."""

from typing import ClassVar

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class OrgProviderOverride(CubeplexBase, table=True):
    """Sparse per-org override for system-level provider enabled/disabled state."""

    _PREFIX: ClassVar[str] = "opo"
    __tablename__ = "org_provider_overrides"
    __table_args__ = (UniqueConstraint("org_id", "provider_id", name="uq_org_provider_override"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    provider_id: str = Field(foreign_key="providers.id", max_length=20, index=True)
    enabled: bool = Field(default=False)
