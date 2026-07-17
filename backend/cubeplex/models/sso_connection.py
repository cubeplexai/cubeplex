"""SSO connection model — org-level enterprise SSO configuration."""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class SSOConnection(CubeplexBase, table=True):
    """One SSO connection per organization.

    protocol: "oidc" | "saml"
    status: "active" | "inactive" | "testing"
    provisioning: "auto" | "invite_only"
    config: protocol-specific JSONB (issuer, endpoints, client_id, etc.)
    """

    _PREFIX: ClassVar[str] = "sso"
    __tablename__ = "sso_connections"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_sso_connections_org_id"),
        Index("ix_sso_connections_org_id", "org_id"),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20)
    protocol: str = Field(max_length=10)
    display_name: str = Field(max_length=255)
    status: str = Field(default="inactive", max_length=10)
    provisioning: str = Field(default="auto", max_length=20)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20, nullable=True
    )
    last_idp_attributes: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
