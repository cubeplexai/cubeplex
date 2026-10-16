"""Durable creation outcomes retained across provider deletion."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_PROVIDER_CREATION_RECEIPT


class ProviderCreationReceipt(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_PROVIDER_CREATION_RECEIPT
    __tablename__ = "provider_creation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "created_by_user_id",
            "creation_discovery_id",
            name="uq_provider_creation_receipt_token",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
    creation_discovery_id: str = Field(max_length=64)
    outcome: str = Field(default="created", max_length=16)
    provider_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(20), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
        ),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
