"""Request/response schemas for provider & model admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

WireApi = Literal["openai-completions", "openai-responses", "anthropic-messages"]


class ProviderCreate(BaseModel):
    name: str = Field(max_length=64)
    slug: str | None = Field(default=None, max_length=64)
    provider_type: WireApi = "openai-completions"
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(default="api_key", max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    preset_slug: str | None = Field(default=None, max_length=64)
    capability: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    provider_type: WireApi | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    auth_type: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict[str, Any] | None = None
    extra_headers: dict[str, Any] | None = None
    enabled: bool | None = None
    preset_slug: str | None = None
    capability: dict[str, Any] | None = None
    model_capability_overrides: dict[str, Any] | None = None


class ModelTest(BaseModel):
    model_id: str = Field(max_length=128)


class ProviderLivenessRequest(BaseModel):
    """Pre-save liveness dry-run body (spec §4.3).

    Builds a transient provider from these fields — no row is written. ``model_id``
    is the cheap model the single liveness call is issued against.
    """

    preset_slug: str | None = Field(default=None, max_length=64)
    api: WireApi = "openai-completions"
    base_url: str = Field(max_length=2048)
    api_key: str | None = Field(default=None, max_length=512)
    capability: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, Any] = Field(default_factory=dict)
    model_id: str = Field(max_length=128)


class ProviderTestRequest(ProviderLivenessRequest):
    """Pre-save full-probe dry-run body (spec §4.3).

    Same transient-provider construction as liveness, but runs liveness + the
    per-model capability probe and returns the composed ``ProbeResult``.
    """


class ProviderTestStreamRequest(BaseModel):
    """Explicit model DB ids to test (wizard models are enabled=false)."""

    model_db_ids: list[str] = Field(min_length=1)


class OrgProviderOverrideUpdate(BaseModel):
    enabled: bool


class OrgProviderOverrideOut(BaseModel):
    enabled: bool


class ModelCreate(BaseModel):
    model_id: str = Field(max_length=128)
    display_name: str = Field(max_length=128)
    reasoning: bool = False
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_write: float = 0.0
    context_window: int
    max_tokens: int
    extra_body: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    reasoning: bool | None = None
    input_modalities: list[str] | None = None
    cost_input: float | None = None
    cost_output: float | None = None
    cost_cache_read: float | None = None
    cost_cache_write: float | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] | None = None
    extra_headers: dict[str, Any] | None = None
    enabled: bool | None = None


class ModelOut(BaseModel):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    reasoning: bool
    input_modalities: list[str]
    cost_input: float
    cost_output: float
    cost_cache_read: float
    cost_cache_write: float
    context_window: int
    max_tokens: int
    extra_body: dict[str, Any]
    extra_headers: dict[str, Any]
    enabled: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class ModelReadinessOut(ModelOut):
    """A model row plus its per-model test status and server-derived readiness.

    Returned by GET /admin/providers/{id} so the UI reads `readiness` verbatim
    (it never re-derives). `last_test_at` is a UTC-offset ISO string.
    """

    last_test_at: str | None = None
    last_test_status: str | None = None  # "ok" | "warn" | "fail" | "unavailable"
    last_test_summary: dict[str, Any] = Field(default_factory=dict)
    # §4.1: "ready"|"degraded"|"stale"|"model_error"|"unavailable"|"provider_error"
    readiness: str


class ProviderOut(BaseModel):
    id: str
    name: str
    slug: str
    # Read-side: reflects the DB column (a plain str). The 3-literal contract is
    # enforced on the write path (ProviderCreate/ProviderUpdate), so stored values
    # are already canonical; no need to re-validate on every read.
    provider_type: str
    base_url: str
    auth_type: str
    has_api_key: bool
    logo_url: str | None
    enabled: bool
    is_system: bool
    model_count: int
    models: list[ModelReadinessOut] | None = None
    org_override: OrgProviderOverrideOut | None = None
    extra_body: dict[str, Any]
    extra_headers: dict[str, Any]
    preset_slug: str | None = None
    # Brand-icon id resolved from the preset catalog so the configured-only
    # provider UI can render the brand glyph without fetching presets.
    logo: str | None = None
    capability: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, Any] = Field(default_factory=dict)
    # Provider-level liveness/credential status (spec §4.1). UTC-offset ISO string.
    last_liveness_at: str | None = None
    last_liveness_status: str | None = None  # "ok" | "fail"
    last_liveness_summary: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime
