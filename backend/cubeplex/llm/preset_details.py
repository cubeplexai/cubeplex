"""Resolve workspace preset summary detail fields from a primary ref + snapshot.

Used by the workspace model-presets listing so the chat picker can show
model-family context without N+1 admin calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cubeplex.llm.config import ModelConfig, ProviderConfig


@dataclass(frozen=True, slots=True)
class PresetModelDetails:
    provider_slug: str | None
    model_id: str | None
    model_display_name: str | None
    context_window: int | None
    reasoning: bool | None
    input_modalities: list[str] | None


def split_primary(primary: str) -> tuple[str | None, str | None]:
    """Split ``slug/model_id`` on the first ``/`` only.

    Nested model ids (``vendor/model/v1``) keep everything after the first
    slash as ``model_id``. Missing slash → ``(primary, None)``. Empty →
    ``(None, None)``. Never raises.
    """
    if not primary:
        return None, None
    if "/" not in primary:
        return primary, None
    slug, model_id = primary.split("/", 1)
    return (slug or None), (model_id or None)


def find_model(
    providers: Mapping[str, ProviderConfig],
    provider_slug: str | None,
    model_id: str | None,
) -> ModelConfig | None:
    """Look up a model in the snapshot by provider slug + model id."""
    if not provider_slug or not model_id:
        return None
    pc = providers.get(provider_slug)
    if pc is None:
        return None
    for m in pc.models:
        if m.id == model_id:
            return m
    return None


def detail_fields(
    providers: Mapping[str, ProviderConfig],
    primary: str,
) -> PresetModelDetails:
    """Resolve model detail fields for a preset primary ref."""
    slug, model_id = split_primary(primary)
    mc = find_model(providers, slug, model_id)
    if mc is None:
        return PresetModelDetails(
            provider_slug=slug,
            model_id=model_id,
            model_display_name=None,
            context_window=None,
            reasoning=None,
            input_modalities=None,
        )
    return PresetModelDetails(
        provider_slug=slug,
        model_id=model_id,
        model_display_name=mc.name,
        context_window=mc.context_window,
        reasoning=mc.reasoning,
        input_modalities=list(mc.input),
    )
