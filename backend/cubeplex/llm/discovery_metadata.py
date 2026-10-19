"""Optional metadata enrichment, scoped to the complete advertised ID set."""

from __future__ import annotations

import math
from typing import Any, cast

from pydantic import JsonValue

from cubeplex.llm.catalog.types import ResolvedEndpoint
from cubeplex.llm.discovery_resolver import add_evidence, add_metadata, refresh_eligibility
from cubeplex.llm.discovery_transport import DiscoveryFailure, DiscoveryTransport, instance_base
from cubeplex.llm.discovery_types import (
    CallingConfiguration,
    DiscoveredModel,
    DiscoveryDiagnostic,
    DiscoveryPolicy,
)
from cubeplex.llm.models_dev import apply_models_dev


def apply_catalog(models: list[DiscoveredModel], endpoint: ResolvedEndpoint | None) -> None:
    if endpoint is None:
        return
    references = {m.model_id: m for m in endpoint.models}
    for model in models:
        reference = references.get(model.model_id)
        if reference is None:
            continue
        add_metadata(
            model,
            {
                "purpose": "chat",
                "context_window": reference.context_window,
                "max_tokens": reference.max_tokens,
                "input_modalities": reference.input_modalities,
                "reasoning": reference.reasoning,
            },
            f"catalog:{endpoint.preset_key}",
        )
        add_metadata(
            model,
            {f"cost_{key}": value for key, value in reference.pricing.model_dump().items()},
            f"catalog:{endpoint.preset_key}:price",
            state="suggested",
        )
        if reference.reasoning and endpoint.capability.reasoning:
            add_evidence(
                model,
                "capability",
                cast(JsonValue, endpoint.capability.model_dump()),
                f"catalog:{endpoint.preset_key}",
            )


def apply_openrouter(model: DiscoveredModel, raw: dict[str, Any]) -> None:
    architecture = raw.get("architecture")
    top_provider = raw.get("top_provider")
    normalized: dict[str, Any] = {}
    if isinstance(architecture, dict):
        normalized["input_modalities"] = architecture.get("input_modalities")
        normalized["output_modalities"] = architecture.get("output_modalities")
    if isinstance(top_provider, dict):
        normalized["max_completion_tokens"] = top_provider.get("max_completion_tokens")
    prices = raw.get("pricing")
    if isinstance(prices, dict):
        for field, key in {
            "cost_input": "prompt",
            "cost_output": "completion",
            "cost_cache_read": "input_cache_read",
            "cost_cache_write": "input_cache_write",
        }.items():
            try:
                value = float(prices[key]) * 1_000_000
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                normalized[field] = value
    add_metadata(model, normalized, "openrouter:models")


async def enrich_models(
    models: list[DiscoveredModel],
    rows: list[dict[str, Any]],
    config: CallingConfiguration,
    policy: DiscoveryPolicy,
    http: DiscoveryTransport,
    *,
    selected_ids: list[str],
) -> list[DiscoveryDiagnostic]:
    by_id = {model.model_id: model for model in models}
    diagnostics: list[DiscoveryDiagnostic] = []
    if policy.metadata_profile == "openrouter":
        for row in rows:
            apply_openrouter(by_id[row["id"]], row)
    if policy.metadata_profile == "cliproxyapi":
        for model in models:
            apply_models_dev(model, config)
    if policy.metadata_profile == "ollama" and selected_ids:
        try:
            base = instance_base(config.base_url)
            for model_id in selected_ids:
                data = await http.request("POST", base + "/api/show", body={"model": model_id})
                info = data.get("model_info", {})
                capabilities = data.get("capabilities", [])
                ollama_metadata: dict[str, Any] = {}
                if isinstance(info, dict):
                    for name, value in info.items():
                        if name.endswith(".context_length"):
                            ollama_metadata["context_window"] = value
                if isinstance(capabilities, list) and "completion" in capabilities:
                    ollama_metadata["purpose"] = "chat"
                    ollama_metadata["input_modalities"] = (
                        ["text", "image"] if "vision" in capabilities else ["text"]
                    )
                add_metadata(by_id[model_id], ollama_metadata, "ollama:show")
        except DiscoveryFailure as exc:
            diagnostics.append(DiscoveryDiagnostic(code="enrichment_warning", message=str(exc)))
    for model in models:
        refresh_eligibility(model)
    return diagnostics
