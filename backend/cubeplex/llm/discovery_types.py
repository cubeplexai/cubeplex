"""Typed endpoint policies and normalized discovery evidence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

DiscoveryAdapter = Literal["static", "openai", "anthropic", "ollama"]
MetadataProfile = Literal["generic", "cliproxyapi", "openrouter", "ollama"]
EvidenceState = Literal["declared", "suggested", "confirmed", "unknown", "conflict", "existing"]
ModelPurpose = Literal["chat", "embedding", "image", "audio", "unknown"]
ModelField = Literal[
    "purpose",
    "context_window",
    "max_tokens",
    "input_modalities",
    "output_modalities",
    "reasoning",
    "reasoning_levels",
    "capability",
    "cost_input",
    "cost_output",
    "cost_cache_read",
    "cost_cache_write",
]


class DiscoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: DiscoveryAdapter = "static"
    path: str = "models"
    url_mode: Literal["relative", "instance", "origin"] = "relative"
    auth: Literal["none", "bearer", "anthropic"] = "none"
    pagination: Literal["none", "anthropic", "ollama"] = "none"
    metadata_profile: MetadataProfile = "generic"
    revision: str = "1"
    reference: str | None = None
    root_api_path: str = ""

    @model_validator(mode="after")
    def validate_contract(self) -> DiscoveryPolicy:
        if self.adapter != "static" and not self.reference:
            raise ValueError("live discovery requires a verification reference")
        if self.adapter == "anthropic" and self.auth != "anthropic":
            raise ValueError("Anthropic discovery requires Anthropic authentication")
        if self.adapter != "anthropic" and self.pagination == "anthropic":
            raise ValueError("Anthropic pagination requires the Anthropic adapter")
        if self.adapter == "ollama" and self.metadata_profile != "ollama":
            raise ValueError("Ollama discovery requires its metadata profile")
        if "://" in self.path or ".." in self.path.split("/"):
            raise ValueError("discovery paths must stay on the configured instance")
        return self


class CallingConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api: Literal["openai-completions", "openai-responses", "anthropic-messages"] = (
        "openai-completions"
    )
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=512, repr=False, exclude=True)
    auth_type: Literal["api_key", "bearer_token", "none"] = "api_key"
    preset_slug: str | None = Field(default=None, max_length=64)
    discovery: DiscoveryPolicy | None = None
    capability: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict, repr=False)


class EvidenceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: JsonValue = None
    source: str
    state: EvidenceState = "declared"
    revision: str | None = None
    observed_at: AwareDatetime | None = None


class FieldEvidence(EvidenceDeclaration):
    alternatives: list[EvidenceDeclaration] = Field(default_factory=list)


class DiscoveryDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retry_after: str | None = None


class DiscoveredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    display_name: str
    purpose: ModelPurpose = "unknown"
    canonical_model_id: str | None = None
    fields: dict[ModelField, FieldEvidence] = Field(default_factory=dict)
    eligible: bool = False
    missing_fields: list[ModelField] = Field(default_factory=list)
    diagnostics: list[DiscoveryDiagnostic] = Field(default_factory=list)
    existing_model_db_id: str | None = None


class DiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "not_supported", "failed"]
    availability_source: Literal["live", "preset"] = "live"
    discovery_id: str | None = None
    observed_at: AwareDatetime
    candidates: list[DiscoveredModel] = Field(default_factory=list)
    diagnostics: list[DiscoveryDiagnostic] = Field(default_factory=list)


class DiscoveryPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: str
    actor_user_id: str
    provider_id: str | None = None
    fingerprint: str
    expires_at: AwareDatetime
    response: DiscoveryResponse
