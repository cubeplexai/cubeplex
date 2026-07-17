"""Provider and Model — LLM provider/model configuration tables."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class Provider(CubeplexBase, table=True):
    """LLM provider — system-level (org_id=NULL) or org-specific."""

    _PREFIX: ClassVar[str] = "prv"
    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_provider_org_name"),
        Index(
            "uq_provider_org_slug",
            "org_id",
            "slug",
            unique=True,
            postgresql_where="org_id IS NOT NULL",
        ),
        Index(
            "uq_provider_system_slug",
            "slug",
            unique=True,
            postgresql_where="org_id IS NULL",
        ),
    )

    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True
    )
    name: str = Field(max_length=64)
    slug: str = Field(max_length=64, index=True)
    provider_type: str = Field(default="openai-completions", max_length=32)
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(default="api_key", max_length=32)
    credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20, nullable=True, index=True
    )
    oauth_client_id: str | None = Field(default=None, max_length=256)
    oauth_client_secret: str | None = Field(default=None, max_length=256)
    oauth_auth_url: str | None = Field(default=None, max_length=2048)
    oauth_token_url: str | None = Field(default=None, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    preset_slug: str | None = Field(default=None, max_length=64)
    capability: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    model_capability_overrides: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Provider-level test = liveness/credential ONLY (spec §4.1).
    last_liveness_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_liveness_status: str | None = Field(
        default=None, max_length=16
    )  # "ok" | "auth_error" | "fail"
    last_liveness_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )


class Model(CubeplexBase, table=True):
    """LLM model — belongs to a provider."""

    _PREFIX: ClassVar[str] = "mdl"
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_model_provider_model"),)

    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True
    )
    provider_id: str = Field(foreign_key="providers.id", max_length=20, index=True)
    model_id: str = Field(max_length=128)
    display_name: str = Field(max_length=128)
    reasoning: bool = Field(default=False)
    input_modalities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    cost_input: float = Field(default=0.0)
    cost_output: float = Field(default=0.0)
    cost_cache_read: float = Field(default=0.0)
    cost_cache_write: float = Field(default=0.0)
    context_window: int = Field()
    max_tokens: int = Field()
    extra_body: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    # Per-model test = capability probe + model existence (spec §4.1).
    # "ok" | "warn" | "fail" | "unavailable".
    last_test_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_test_status: str | None = Field(default=None, max_length=16)
    last_test_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
