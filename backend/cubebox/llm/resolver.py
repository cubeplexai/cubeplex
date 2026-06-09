"""Pure resolver — turns an LLMSnapshot + caller intent into an LLMPreset.

Functions are sync, no I/O, no cubepi imports. Tests construct snapshots
directly.
"""

from cubebox.llm.errors import (
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
        for p in snap.presets:
            if p.is_default:
                return p
        raise NoDefaultPresetError()
    for p in snap.presets:
        if p.label == label:
            return p
    raise UnknownPresetError(label)


def resolve_task_preset(snap: LLMSnapshot, task: str) -> LLMPreset:
    label = snap.task_presets.get(task)
    if label is not None:
        for p in snap.presets:
            if p.label == label:
                return p
    return resolve_preset(snap, None)
