"""Catalog source-schema + resolved/derived types. Spec §4."""

from __future__ import annotations

from typing import Any, Literal

from cubepi.providers.capability import CapabilityDescriptor
from pydantic import BaseModel, Field

# The protocols cubeplex offers in its catalog. Mirrors cubepi's WireApi but
# declared locally so the catalog does not import cubepi's (to-be-deleted)
# catalog package. See spec §3 "WireApi decoupling".
WireApi = Literal["anthropic-messages", "openai-completions", "openai-responses"]

# A model's plan membership: a single plan, a list, or None (untagged vendor).
PlanRef = str | list[str] | None


class Pricing(BaseModel):
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelPreset(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    max_tokens: int
    input_modalities: list[str]
    reasoning: bool = False
    plan: PlanRef = None
    pricing: Pricing

    def plans(self) -> list[str] | None:
        """Normalized plan list, or None for untagged."""
        if self.plan is None:
            return None
        return [self.plan] if isinstance(self.plan, str) else list(self.plan)


class Region(BaseModel):
    host: str


class Endpoint(BaseModel):
    region: str
    protocol: WireApi
    plan: str | None = None
    path: str = ""
    host: str | None = None  # overrides region host (§4.1)
    base_url: str | None = None  # full override, bypasses composition (§4.1)
    capability: str | dict[str, Any]  # profile name (str) or inline descriptor (dict)
    key: str | None = None  # optional preset_key override (§4.4)


class Vendor(BaseModel):
    vendor: str
    display_name: str
    short_name: str
    logo: str | None = None
    category: Literal["saas", "oss-framework", "custom"]
    description: str
    regions: dict[str, Region] = Field(default_factory=dict)
    endpoints: list[Endpoint] = Field(default_factory=list)
    models: list[ModelPreset] = Field(default_factory=list)


class ResolvedEndpoint(BaseModel):
    """One flattened endpoint preset — what consumers (seeder/API) read."""

    preset_key: str
    vendor: str
    region: str
    protocol: WireApi
    plan: str | None
    base_url: str
    capability: CapabilityDescriptor
    models: list[ModelPreset]  # the subset serving this endpoint (§4 membership)


class Catalog(BaseModel):
    vendors: list[Vendor]
    endpoints: dict[str, ResolvedEndpoint]  # keyed by preset_key

    def resolve(self, preset_key: str) -> ResolvedEndpoint:
        if preset_key not in self.endpoints:
            raise KeyError(preset_key)
        return self.endpoints[preset_key]

    def to_api(self) -> list[dict[str, Any]]:
        """Nested vendor list for GET /admin/llm/presets (spec §5.1)."""
        out: list[dict[str, Any]] = []
        for v in self.vendors:
            v_eps = [e for e in self.endpoints.values() if e.vendor == v.vendor]
            out.append(
                {
                    "vendor": v.vendor,
                    "display_name": v.display_name,
                    "short_name": v.short_name,
                    "logo": v.logo,
                    "category": v.category,
                    "description": v.description,
                    "endpoints": [
                        {
                            "preset_key": e.preset_key,
                            "region": e.region,
                            "protocol": e.protocol,
                            "plan": e.plan,
                            "base_url": e.base_url,
                            "model_ids": [m.model_id for m in e.models],
                            # Resolved capability so the wizard can prefill the editor
                            # and send it back only when the user overrides it.
                            "capability": e.capability.model_dump(mode="json"),
                        }
                        for e in v_eps
                    ],
                    "models": [
                        {
                            "model_id": m.model_id,
                            "display_name": m.display_name,
                            "plan": m.plans(),
                            "context_window": m.context_window,
                            "max_tokens": m.max_tokens,
                            "input_modalities": m.input_modalities,
                            "reasoning": m.reasoning,
                            "pricing": m.pricing.model_dump(),
                        }
                        for m in v.models
                    ],
                }
            )
        return out
