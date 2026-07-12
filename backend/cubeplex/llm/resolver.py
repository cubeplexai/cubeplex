"""Pure resolver — turns an LLMSnapshot + caller intent into a ModelPreset.

Functions are sync, no I/O, no cubepi imports. Tests construct snapshots
directly.
"""

from collections.abc import Mapping

from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.errors import (
    BrokenPresetError,
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset


def parse_model_ref(ref: str) -> tuple[str, str]:
    parts = ref.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidModelRefError(ref)
    return parts[0], parts[1]


def resolve_model_preset(snap: LLMSnapshot, key: str | None) -> ModelPreset:
    if key is None:
        preset = next((p for p in snap.model_presets if p.is_default), None)
        if preset is None:
            raise NoDefaultPresetError()
    else:
        preset = next((p for p in snap.model_presets if p.key == key), None)
        if preset is None:
            raise UnknownPresetError(key)
    missing = _missing_refs(preset, snap.providers)
    if missing:
        raise BrokenPresetError(preset.key, missing_refs=missing)
    return preset


def resolve_task_preset(snap: LLMSnapshot, task: str) -> ModelPreset:
    key = snap.task_routing.get(task)
    if key is not None:
        for p in snap.model_presets:
            if p.key == key:
                return p
    return resolve_model_preset(snap, None)


def _missing_refs(preset: ModelPreset, providers: Mapping[str, ProviderConfig]) -> list[str]:
    missing: list[str] = []
    for ref in preset.chain:
        try:
            slug, model_id = ref.split("/", 1)
        except ValueError:
            missing.append(ref)
            continue
        cfg = providers.get(slug)
        if cfg is None or all(m.id != model_id for m in cfg.models):
            missing.append(ref)
    return missing
