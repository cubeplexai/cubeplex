"""Pure resolver — turns an LLMSnapshot + caller intent into an LLMPreset.

Functions are sync, no I/O, no cubepi imports. Tests construct snapshots
directly.
"""

from collections.abc import Mapping

from cubebox.llm.config import ProviderConfig
from cubebox.llm.errors import (
    BrokenPresetError,
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubebox.llm.snapshot import LLMPreset, LLMSnapshot


def parse_model_ref(ref: str) -> tuple[str, str]:
    parts = ref.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidModelRefError(ref)
    return parts[0], parts[1]


def resolve_preset(snap: LLMSnapshot, label: str | None) -> LLMPreset:
    if label is None:
        preset = next((p for p in snap.presets if p.is_default), None)
        if preset is None:
            raise NoDefaultPresetError()
    else:
        preset = next((p for p in snap.presets if p.label == label), None)
        if preset is None:
            raise UnknownPresetError(label)
    missing = _missing_refs(preset, snap.providers)
    if missing:
        raise BrokenPresetError(preset.label, missing_refs=missing)
    return preset


def resolve_task_preset(snap: LLMSnapshot, task: str) -> LLMPreset:
    label = snap.task_presets.get(task)
    if label is not None:
        for p in snap.presets:
            if p.label == label:
                return p
    return resolve_preset(snap, None)


def _missing_refs(preset: LLMPreset, providers: Mapping[str, ProviderConfig]) -> list[str]:
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
