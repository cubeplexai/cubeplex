"""Provider and Model — LLM provider/model configuration tables."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Provider(SQLModel, table=True):
    """LLM provider — system-level (org_id=NULL) or org-specific."""

    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_provider_org_name"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)
    name: str = Field(max_length=64)
    provider_type: str = Field(default="openai_compat", max_length=32)
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(default="api_key", max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    # TODO(vault): Encrypt api_key when M1-E4 vault integration lands.
    oauth_client_id: str | None = Field(default=None, max_length=256)
    oauth_client_secret: str | None = Field(default=None, max_length=256)
    oauth_auth_url: str | None = Field(default=None, max_length=2048)
    oauth_token_url: str | None = Field(default=None, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Model(SQLModel, table=True):
    """LLM model — belongs to a provider."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_model_provider_model"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)
    provider_id: str = Field(max_length=36, index=True)
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
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
