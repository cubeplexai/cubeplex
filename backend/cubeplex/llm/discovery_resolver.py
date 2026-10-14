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

_PURPOSES = {"chat", "embedding", "image", "audio", "unknown"}
_ACCEPTED = {"declared", "confirmed", "existing"}
_PRIORITY = {"unknown": 0, "suggested": 1, "existing": 2, "declared": 3, "confirmed": 4}


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


def refresh_eligibility(model: DiscoveredModel) -> None:
    missing: list[ModelField] = []
    purpose = model.fields.get("purpose")
    model.purpose = (
        cast(ModelPurpose, purpose.value)
        if purpose
        and isinstance(purpose.value, str)
        and purpose.value in _PURPOSES
        and purpose.state in _ACCEPTED
        else "unknown"
    )
    if model.purpose == "unknown":
        missing.append("purpose")
    for key in ("context_window", "max_tokens"):
        field = model.fields.get(key)
        if (
            not field
            or field.state not in _ACCEPTED
            or type(field.value) is not int
            or field.value <= 0
        ):
            missing.append(key)
    inputs = model.fields.get("input_modalities")
    if (
        not inputs
        or inputs.state not in _ACCEPTED
        or not isinstance(inputs.value, list)
        or not inputs.value
        or "text" not in inputs.value
        or not all(isinstance(v, str) for v in inputs.value)
    ):
        missing.append("input_modalities")
    context = model.fields.get("context_window")
    output = model.fields.get("max_tokens")
    if context and output and type(context.value) is int and type(output.value) is int:
        if output.value > context.value and "max_tokens" not in missing:
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
