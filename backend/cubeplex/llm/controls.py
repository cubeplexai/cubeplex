"""Controls and input types that every active model in a chain can implement."""

from __future__ import annotations

from dataclasses import dataclass

from cubeloop.providers.base import ReasoningControl
from cubeloop.providers.capability import CapabilityDescriptor

from cubeplex.llm.config import ModelConfig
from cubeplex.llm.resolver import parse_model_ref
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset


@dataclass(frozen=True)
class ChainControls:
    modes: tuple[str, ...]
    efforts: tuple[str, ...]
    input_modalities: tuple[str, ...]


def reasoning_support(model: ModelConfig) -> bool | None:
    evidence = model.configuration_metadata.get("fields", {}).get("reasoning", {})
    value = evidence.get("value")
    if isinstance(value, bool) and evidence.get("state") in {"declared", "confirmed", "existing"}:
        return value
    if model.configuration_metadata.get("origin") == "discovery":
        return None
    return model.reasoning


def chain_controls(snapshot: LLMSnapshot, preset: ModelPreset) -> ChainControls:
    shared_modes: set[str] | None = None
    shared_efforts: set[str] | None = None
    shared_inputs: set[str] | None = None
    for ref in preset.chain:
        slug, model_id = parse_model_ref(ref)
        provider = snapshot.providers.get(slug)
        model = next((m for m in provider.models if m.id == model_id), None) if provider else None
        if model is None or provider is None:
            return ChainControls((), (), ())
        capability = CapabilityDescriptor.model_validate(
            provider.model_capability_overrides.get(model_id, provider.capability)
        )
        mapping = capability.reasoning
        modes: set[str] = set(mapping.mode_payloads) if mapping else set()
        if not model.reasoning:
            modes -= {"on", "auto"}
        efforts: set[str] = set(mapping.effort_values) if mapping and mapping.effort_path else set()
        inputs = set(model.input) & {"text", "image"}
        if model.configuration_metadata.get("origin") == "discovery":
            if not capability.supports_images or not model.image_verified:
                inputs.discard("image")
        shared_modes = modes if shared_modes is None else shared_modes & modes
        shared_efforts = efforts if shared_efforts is None else shared_efforts & efforts
        shared_inputs = inputs if shared_inputs is None else shared_inputs & inputs
    return ChainControls(
        tuple(sorted(shared_modes or ())),
        tuple(sorted(shared_efforts or ())),
        tuple(sorted(shared_inputs or ())),
    )


def validate_reasoning_for_chain(
    snapshot: LLMSnapshot, preset: ModelPreset, control: ReasoningControl | None
) -> None:
    if control is None:
        return
    allowed = chain_controls(snapshot, preset)
    if control.mode not in allowed.modes:
        raise ValueError("This reasoning mode is not available for the selected model chain")
    if control.mode != "off" and bool(allowed.efforts) and control.effort not in allowed.efforts:
        raise ValueError("This reasoning effort is not available for the selected model chain")
    if control.summary != "none":
        for ref in preset.chain:
            slug, model_id = parse_model_ref(ref)
            provider = snapshot.providers[slug]
            mapping = CapabilityDescriptor.model_validate(
                provider.model_capability_overrides.get(model_id, provider.capability)
            ).reasoning
            if (
                mapping is None
                or mapping.summary_path is None
                or control.summary not in mapping.summary_values
            ):
                raise ValueError(
                    "This reasoning summary is not available for the selected model chain"
                )
