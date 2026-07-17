"""External identity model — maps IdP identities to cubeplex users."""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class ExternalIdentity(CubeplexBase, table=True):
    """Links an external identity (OIDC sub / SAML NameID / Google sub)
    to a cubeplex User. One user can have multiple external identities
    (e.g. SSO + Google).

    provider_type: "oidc_sso" | "saml_sso" | "google"
    provider_id: sso_connection.id for enterprise, "google" for social
    external_id: IdP-side user identifier
    """

    _PREFIX: ClassVar[str] = "eid"
    __tablename__ = "external_identities"
    __table_args__ = (
        Index(
            "uq_external_identity_provider",
            "provider_type",
            "provider_id",
            "external_id",
            unique=True,
        ),
        Index("ix_external_identities_user_id", "user_id"),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20)
    provider_type: str = Field(max_length=20)
    provider_id: str = Field(max_length=20)
    external_id: str = Field(max_length=512)
    external_email: str = Field(max_length=320)
    metadata_: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON))
