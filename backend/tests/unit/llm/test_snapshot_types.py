"""LLMSnapshot / LLMPreset frozen dataclass behavior."""

import pytest

from cubebox.llm.snapshot import LLMPreset, LLMSnapshot


def test_preset_is_frozen():
    p = LLMPreset(label="x", chain=("a/b",), is_default=True)
    with pytest.raises(AttributeError):
        p.label = "y"  # type: ignore[misc]


def test_snapshot_is_frozen():
    s = LLMSnapshot(providers={}, presets=(), task_presets={})
    with pytest.raises(AttributeError):
        s.providers = {}  # type: ignore[misc]


def test_snapshot_holds_data_unchanged():
    p = LLMPreset(label="x", chain=("a/b", "c/d"), is_default=True)
    s = LLMSnapshot(providers={}, presets=(p,), task_presets={"title": "x"})
    assert s.presets[0].chain == ("a/b", "c/d")
    assert s.task_presets == {"title": "x"}
