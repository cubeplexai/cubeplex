"""Per-run egress placeholder reference. Stores only hash(placeholder)."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_EGRESS_REF


class EgressRef(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_EGRESS_REF
    __tablename__ = "egress_refs"
    __table_args__ = (
        Index("ix_egress_ref_hash", "ref_hash", unique=True),
        Index("ix_egress_ref_sandbox", "sandbox_id"),
    )

    ref_hash: str = Field(max_length=64)
    sandbox_id: str = Field(max_length=64)
    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20)
    user_id: str = Field(foreign_key="users.id", max_length=20)
    run_id: str | None = Field(default=None, max_length=64, nullable=True)
    bindings: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="valid", max_length=16)
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
