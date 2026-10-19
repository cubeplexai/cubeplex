"""Offline models.dev provider snapshot used to fill discovery fields."""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from cubeplex.llm.discovery_resolver import add_evidence, add_metadata
from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveredModel

_SNAPSHOT = Path(__file__).parent / "catalog" / "data" / "models_dev_snapshot.json"
_OFFICIAL = ("anthropic", "openai", "google", "xai")
_SUFFIXES = ("thinking", "xhigh", "minimal", "medium", "high", "low", "max")
_EFFORTS = {"minimal", "low", "medium", "high", "max"}
_SOURCE = "https://models.dev/api.json"

CatalogIndex = dict[str, list[tuple[str, dict[str, Any]]]]


def reduce_provider_catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep provider invocation IDs and the fields discovery can apply.

    An ID with no usable fields is still kept. An exact catalog ID must block
    the suffix fallback, even when that record cannot fill a configuration.
    """
    providers: dict[str, dict[str, Any]] = {}
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        reduced: dict[str, Any] = {}
        for model_id, raw in models.items():
            if not isinstance(model_id, str) or not model_id or not isinstance(raw, dict):
                continue
            reduced[model_id] = _reduce_model(raw)
        if reduced:
            providers[provider_id] = reduced
    return providers


def lookup_model(
    model_id: str, index: CatalogIndex | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Match an advertised ID. A known suffix is removed only when the full ID misses."""
    catalog = index if index is not None else _load_index()
    exact = catalog.get(model_id)
    if exact:
        return _choose(exact)
    for suffix in _SUFFIXES:
        for separator in ("-", ":"):
            token = separator + suffix
            if model_id.endswith(token) and len(model_id) > len(token):
                base = catalog.get(model_id[: -len(token)])
                if base:
                    return _choose(base)
    return None


def apply_models_dev(
    model: DiscoveredModel,
    config: CallingConfiguration,
    index: CatalogIndex | None = None,
) -> None:
    match = lookup_model(model.model_id, index)
    if match is None:
        return
    provider, record = match
    source = f"models.dev:{provider}"
    raw: dict[str, Any] = {}
    if isinstance(record.get("context"), int):
        raw["context_window"] = record["context"]
    if isinstance(record.get("output"), int):
        raw["max_tokens"] = record["output"]
    for field, key in (
        ("input_modalities", "input"),
        ("output_modalities", "output_modalities"),
    ):
        value = record.get(key)
        if isinstance(value, list) and value:
            raw[field] = value
    if record.get("reasoning") is True:
        raw["reasoning"] = True
    efforts = [level for level in record.get("efforts") or [] if level in _EFFORTS]
    if efforts:
        raw["thinking"] = {"levels": efforts}
    for key in ("cost_input", "cost_output", "cost_cache_read", "cost_cache_write"):
        rate = record.get(key)
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
            raw[key] = rate
    add_metadata(model, raw, source)
    if efforts:
        _apply_efforts(model, config, efforts, source)


def _reduce_model(raw: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    limit = raw.get("limit")
    if isinstance(limit, dict):
        context = limit.get("context")
        output = limit.get("output")
        if type(context) is int and context > 0:
            record["context"] = context
        if type(output) is int and output > 0:
            record["output"] = output
    modalities = raw.get("modalities")
    if isinstance(modalities, dict):
        for source, target in (("input", "input"), ("output", "output_modalities")):
            values = modalities.get(source)
            if isinstance(values, list) and values and all(isinstance(v, str) for v in values):
                record[target] = sorted({v.lower() for v in values})
    if raw.get("reasoning") is True:
        record["reasoning"] = True
    efforts = _efforts(raw.get("reasoning_options"))
    if efforts:
        record["efforts"] = efforts
    cost = raw.get("cost")
    if isinstance(cost, dict):
        for source, target in (
            ("input", "cost_input"),
            ("output", "cost_output"),
            ("cache_read", "cost_cache_read"),
            ("cache_write", "cost_cache_write"),
        ):
            rate = cost.get(source)
            if isinstance(rate, (int, float)) and not isinstance(rate, bool):
                if math.isfinite(rate) and rate > 0:
                    record[target] = rate
    return record


def _efforts(options: Any) -> list[str]:
    if not isinstance(options, list):
        return []
    found: list[str] = []
    for option in options:
        if not isinstance(option, dict) or option.get("type") != "effort":
            continue
        values = option.get("values")
        if not isinstance(values, list):
            continue
        found.extend(value for value in values if isinstance(value, str) and value in _EFFORTS)
    return list(dict.fromkeys(found))


def _choose(candidates: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    def rank(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
        provider = item[0]
        if provider in _OFFICIAL:
            return (0, _OFFICIAL.index(provider), provider)
        return (1, len(_OFFICIAL), provider)

    return min(candidates, key=rank)


def _apply_efforts(
    model: DiscoveredModel,
    config: CallingConfiguration,
    efforts: list[str],
    source: str,
) -> None:
    path = "reasoning.effort" if config.api == "openai-responses" else "reasoning_effort"
    modes: dict[str, Any] = {"on": {}}
    capability = dict(config.capability)
    capability["reasoning"] = {
        "mode_payloads": modes,
        "effort_path": path,
        "effort_values": {level: level for level in efforts},
        "apply_effort_when_off": False,
        "unsupported_mode_policy": "error",
    }
    add_evidence(model, "capability", cast(JsonValue, capability), source)


@cache
def _load_index() -> CatalogIndex:
    document = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    providers = document.get("providers")
    index: CatalogIndex = {}
    if not isinstance(providers, dict):
        return index
    for provider, models in providers.items():
        if not isinstance(provider, str) or not isinstance(models, dict):
            continue
        for model_id, record in models.items():
            if isinstance(model_id, str) and isinstance(record, dict):
                index.setdefault(model_id, []).append((provider, record))
    return index


def snapshot_path() -> Path:
    return _SNAPSHOT


def snapshot_source() -> str:
    return _SOURCE
