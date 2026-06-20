"""LLMSnapshot / ModelPreset frozen dataclass behavior."""

import pytest

from cubebox.llm.snapshot import LLMSnapshot, ModelPreset


def test_preset_is_frozen():
    p = ModelPreset(key="x", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    with pytest.raises(AttributeError):
        p.key = "y"  # type: ignore[misc]


def test_snapshot_is_frozen():
    s = LLMSnapshot(providers={}, model_presets=(), task_routing={})
    with pytest.raises(AttributeError):
        s.providers = {}  # type: ignore[misc]


def test_snapshot_holds_data_unchanged():
    p = ModelPreset(
        key="x", primary="a/b", fallbacks=("c/d",), kind="tier", is_default=True
    )
    s = LLMSnapshot(providers={}, model_presets=(p,), task_routing={"title": "x"})
    assert s.model_presets[0].chain == ("a/b", "c/d")
    assert s.task_routing == {"title": "x"}
