"""Resolve field evidence without guessing model identity from its name."""

from __future__ import annotations

import json
import math
from typing import Any, cast

from pydantic import JsonValue

from cubeplex.llm.discovery_types import (
    DiscoveredModel,
    EvidenceDeclaration,
    EvidenceState,
    FieldEvidence,
    ModelField,
    ModelPurpose,
)

_PURPOSES = {"chat", "embedding", "image", "audio", "video", "unknown"}
_ACCEPTED = {"declared", "confirmed", "existing"}
_PRIORITY = {"unknown": 0, "suggested": 1, "existing": 2, "declared": 3, "confirmed": 4}


def _catalog_source(source: str) -> bool:
    return source.startswith("catalog:") or source.startswith("models.dev:")


def listing_catalog_default(field: FieldEvidence) -> EvidenceDeclaration | None:
    """Listing value when that is the only disagreement with catalog entries.

    The field stays in conflict so an administrator can still choose the catalog.
    """
    if field.state != "conflict":
        return None
    listing = [
        item for item in field.alternatives if item.source == "listing" and item.value is not None
    ]
    others = [item for item in field.alternatives if item.source != "listing"]
    catalog_only = bool(others) and all(_catalog_source(item.source) for item in others)
    if len(listing) != 1 or not catalog_only:
        return None
    return listing[0]


def accepted_declaration(field: FieldEvidence | None) -> EvidenceDeclaration | None:
    if field is None:
        return None
    chosen = listing_catalog_default(field)
    if chosen is not None:
        return chosen
    if field.state in _ACCEPTED:
        return field
    return None


def add_evidence(
    model: DiscoveredModel,
    field: ModelField,
    value: JsonValue,
    source: str,
    state: EvidenceState = "declared",
    revision: str | None = None,
) -> None:
    incoming = EvidenceDeclaration(value=value, source=source, state=state, revision=revision)
    previous = model.fields.get(field)
    if previous is None or previous.value is None:
        model.fields[field] = FieldEvidence(**incoming.model_dump())
        return
    declarations = previous.alternatives or [
        EvidenceDeclaration(**previous.model_dump(exclude={"alternatives"}))
    ]
    if any(d.value == value and d.source == source for d in declarations):
        return
    declarations.append(incoming)
    preferred = max(declarations, key=lambda d: _PRIORITY.get(d.state, 0))
    different = {json.dumps(d.value, sort_keys=True) for d in declarations}
    model.fields[field] = FieldEvidence(
        **preferred.model_dump(exclude={"state"}),
        state="conflict" if len(different) > 1 else preferred.state,
        alternatives=declarations,
    )


def _positive_limit(field: FieldEvidence | None) -> int | None:
    chosen = accepted_declaration(field)
    if chosen is None or type(chosen.value) is not int or chosen.value <= 0:
        return None
    return chosen.value


def refresh_eligibility(model: DiscoveredModel) -> None:
    missing: list[ModelField] = []
    purpose = accepted_declaration(model.fields.get("purpose"))
    model.purpose = (
        cast(ModelPurpose, purpose.value)
        if purpose and isinstance(purpose.value, str) and purpose.value in _PURPOSES
        else "unknown"
    )
    if model.purpose == "unknown":
        missing.append("purpose")
    limits: dict[ModelField, int | None] = {
        "context_window": _positive_limit(model.fields.get("context_window")),
        "max_tokens": _positive_limit(model.fields.get("max_tokens")),
    }
    for key, value in limits.items():
        if value is None:
            missing.append(key)
    inputs = accepted_declaration(model.fields.get("input_modalities"))
    if (
        inputs is None
        or not isinstance(inputs.value, list)
        or not inputs.value
        or "text" not in inputs.value
        or not all(isinstance(v, str) for v in inputs.value)
    ):
        missing.append("input_modalities")
    context = limits["context_window"]
    output = limits["max_tokens"]
    if (
        context is not None
        and output is not None
        and output > context
        and "max_tokens" not in missing
    ):
        missing.append("max_tokens")
    model.missing_fields = missing
    model.eligible = (
        model.purpose == "chat"
        and not missing
        and 0 < len(model.model_id) <= 128
        and 0 < len(model.display_name) <= 128
    )


def add_metadata(
    model: DiscoveredModel,
    raw: dict[str, Any],
    source: str,
    *,
    state: EvidenceState = "declared",
) -> None:
    numeric = {
        "context_window": ("context_window", "context_length", "max_model_len"),
        "max_tokens": (
            "max_tokens",
            "max_completion_tokens",
            "max_output_tokens",
            "outputTokenLimit",
        ),
    }
    for field, aliases in numeric.items():
        for alias in aliases:
            value = raw.get(alias)
            if type(value) is int and value > 0:
                add_evidence(model, cast(ModelField, field), value, source, state)
                break
    for field, aliases in {
        "input_modalities": ("input_modalities", "supportedInputModalities"),
        "output_modalities": ("output_modalities", "supportedOutputModalities"),
    }.items():
        for alias in aliases:
            value = raw.get(alias)
            if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                normalized = sorted({str(v).lower() for v in value})
                add_evidence(
                    model, cast(ModelField, field), cast(JsonValue, normalized), source, state
                )
                if field == "output_modalities":
                    purpose: ModelPurpose = "unknown"
                    if normalized == ["text"]:
                        purpose = "chat"
                    elif "image" in normalized:
                        purpose = "image"
                    elif "audio" in normalized:
                        purpose = "audio"
                    elif "video" in normalized:
                        purpose = "video"
                    add_evidence(model, "purpose", purpose, source, state)
                break
    purpose_value = raw.get("purpose")
    if isinstance(purpose_value, str) and purpose_value in _PURPOSES:
        add_evidence(model, "purpose", purpose_value, source, state)
    reasoning = raw.get("reasoning")
    if isinstance(reasoning, bool):
        add_evidence(model, "reasoning", reasoning, source, state)
    thinking = raw.get("thinking")
    if isinstance(thinking, dict):
        levels = thinking.get("levels")
        if isinstance(levels, list) and all(isinstance(v, str) for v in levels):
            add_evidence(model, "reasoning_levels", cast(JsonValue, levels), source, state)
        if thinking.get("max", 0) or levels:
            add_evidence(model, "reasoning", True, source, state)
    for key in ("cost_input", "cost_output", "cost_cache_read", "cost_cache_write"):
        rate = raw.get(key)
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            if math.isfinite(rate) and rate >= 0:
                add_evidence(model, key, float(rate), source, state)
    refresh_eligibility(model)


def resolve_models(rows: list[dict[str, Any]]) -> list[DiscoveredModel]:
    models: dict[str, DiscoveredModel] = {}
    for row in rows:
        model_id = row["id"]
        if model_id not in models:
            name = row.get("display_name") or row.get("name") or model_id
            models[model_id] = DiscoveredModel(model_id=model_id, display_name=str(name))
        add_metadata(models[model_id], row, "listing")
    return list(models.values())
