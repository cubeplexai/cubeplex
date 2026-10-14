"""Optional metadata enrichment, scoped to the complete advertised ID set."""

from __future__ import annotations

import math
from typing import Any, cast
from urllib.parse import quote

from pydantic import JsonValue

from cubeplex.llm.catalog.types import ResolvedEndpoint
from cubeplex.llm.discovery_resolver import add_evidence, add_metadata, refresh_eligibility
from cubeplex.llm.discovery_transport import (
    MAX_PAGES,
    DiscoveryFailure,
    DiscoveryTransport,
    discovery_url,
    instance_base,
)
from cubeplex.llm.discovery_types import (
    CallingConfiguration,
    DiscoveredModel,
    DiscoveryDiagnostic,
    DiscoveryPolicy,
)

_CLIENT_VERSION = "0.159.3"
_STANDARD_EFFORTS = {"minimal", "low", "medium", "high", "max"}


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


def _apply_cliproxy_definition(
    model: DiscoveredModel, raw: dict[str, Any], channel: str, config: CallingConfiguration
) -> None:
    source = f"cliproxyapi:management:{channel}"
    add_metadata(model, raw, source)
    thinking = raw.get("thinking")
    if channel not in {"codex", "openai", "xai"} or not isinstance(thinking, dict):
        return
    levels = thinking.get("levels")
    if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
        return
    efforts = {level: level for level in levels if level in _STANDARD_EFFORTS}
    if not efforts:
        return
    capability = dict(config.capability)
    path = "reasoning.effort" if config.api == "openai-responses" else "reasoning_effort"
    modes: dict[str, Any] = {"on": {}}
    if "none" in levels:
        modes["off"] = (
            {"reasoning": {"effort": "none"}}
            if config.api == "openai-responses"
            else {"reasoning_effort": "none"}
        )
    capability["reasoning"] = {
        "mode_payloads": modes,
        "effort_path": path,
        "effort_values": efforts,
        "apply_effort_when_off": False,
        "unsupported_mode_policy": "error",
    }
    inputs = model.fields.get("input_modalities")
    capability["supports_images"] = bool(
        inputs and isinstance(inputs.value, list) and "image" in inputs.value
    )
    add_evidence(model, "capability", cast(JsonValue, capability), source)


async def _cliproxy_management(
    models: dict[str, DiscoveredModel],
    config: CallingConfiguration,
    http: DiscoveryTransport,
    key: str,
) -> None:
    base = instance_base(config.base_url) + "/v8/management"
    credentials: dict[str, str] = {}
    for page in range(1, MAX_PAGES + 1):
        data = await http.request(
            "GET",
            base + "/credentials",
            params={"page": page, "page_size": 100},
            management_key=key,
        )
        files = data.get("files")
        if not isinstance(files, list):
            raise DiscoveryFailure("malformed_enrichment", "Management inventory is invalid.")
        for row in files:
            if not isinstance(row, dict) or row.get("disabled") is True:
                continue
            name, channel = row.get("name"), row.get("provider") or row.get("type")
            if isinstance(name, str) and isinstance(channel, str):
                credentials[name] = channel
        more = data.get("has_more")
        if not isinstance(more, bool):
            raise DiscoveryFailure("malformed_enrichment", "Management pagination is invalid.")
        if not more:
            break
    else:
        raise DiscoveryFailure("discovery_limit", "Management inventory exceeds the page limit.")
    members: dict[str, set[str]] = {}
    for name, channel in credentials.items():
        data = await http.request(
            "GET", base + "/credentials/models", params={"name": name}, management_key=key
        )
        rows = data.get("models")
        if not isinstance(rows, list):
            raise DiscoveryFailure(
                "malformed_enrichment", "Credential model identities are invalid."
            )
        available = {
            str(row["id"])
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        members.setdefault(channel, set()).update(available & models.keys())
    for channel, ids in members.items():
        if not ids:
            continue
        data = await http.request(
            "GET",
            base + "/routing/model-definitions/" + quote(channel, safe=""),
            management_key=key,
        )
        definitions = data.get("models")
        if not isinstance(definitions, list):
            raise DiscoveryFailure("malformed_enrichment", "Channel definitions are invalid.")
        for definition in definitions:
            if (
                isinstance(definition, dict)
                and isinstance(definition.get("id"), str)
                and definition["id"] in ids
            ):
                model_id = definition["id"]
                _apply_cliproxy_definition(models[model_id], definition, channel, config)


async def enrich_models(
    models: list[DiscoveredModel],
    rows: list[dict[str, Any]],
    config: CallingConfiguration,
    policy: DiscoveryPolicy,
    http: DiscoveryTransport,
    *,
    selected_ids: list[str],
    management_key: str | None = None,
) -> list[DiscoveryDiagnostic]:
    by_id = {model.model_id: model for model in models}
    diagnostics: list[DiscoveryDiagnostic] = []
    if policy.metadata_profile == "openrouter":
        for row in rows:
            apply_openrouter(by_id[row["id"]], row)
    if policy.metadata_profile == "cliproxyapi":
        try:
            data = await http.request(
                "GET",
                discovery_url(config.base_url, policy),
                params={"client_version": _CLIENT_VERSION},
            )
            templates = data.get("models")
            if not isinstance(templates, list):
                raise DiscoveryFailure(
                    "malformed_enrichment", "Client metadata has an unsupported shape."
                )
            for row in templates:
                if (
                    isinstance(row, dict)
                    and isinstance(row.get("slug"), str)
                    and row["slug"] in by_id
                ):
                    model = by_id[row["slug"]]
                    normalized = dict(row)
                    levels = row.get("supported_reasoning_levels")
                    if isinstance(levels, list):
                        efforts = [
                            entry["effort"]
                            for entry in levels
                            if isinstance(entry, dict) and isinstance(entry.get("effort"), str)
                        ]
                        normalized["thinking"] = {"levels": efforts}
                    add_metadata(
                        model,
                        normalized,
                        f"cliproxyapi:client-template:{_CLIENT_VERSION}",
                        state="suggested",
                    )
        except DiscoveryFailure as exc:
            diagnostics.append(DiscoveryDiagnostic(code="enrichment_warning", message=str(exc)))
        if management_key:
            try:
                await _cliproxy_management(by_id, config, http, management_key)
            except DiscoveryFailure as exc:
                diagnostics.append(DiscoveryDiagnostic(code="enrichment_warning", message=str(exc)))
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
