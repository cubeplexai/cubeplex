"""Unit tests for reflection model resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset
from cubeplex.services.reflection_model import resolve_reflection_model


def _snap(*presets: ModelPreset, task_routing: dict[str, str] | None = None) -> LLMSnapshot:
    return LLMSnapshot(
        providers={},
        model_presets=presets,
        task_routing=task_routing or {},
    )


def test_resolve_reflection_model_uses_memory_task() -> None:
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    mini = ModelPreset(key="lite", primary="c/d", fallbacks=(), kind="tier", is_default=False)
    snap = _snap(default, mini, task_routing={"memory": "lite"})
    fallback = object()
    with patch(
        "cubeplex.services.reflection_model.build_chain_model",
        return_value="bound-lite",
    ) as build:
        out = resolve_reflection_model(snap, fallback_model=fallback, run_id="r1")
    assert out == "bound-lite"
    assert build.call_args.args[1] is mini


def test_resolve_reflection_model_falls_back_on_error() -> None:
    snap = _snap()  # no default → resolve fails
    fallback = MagicMock(name="chat-model")
    out = resolve_reflection_model(snap, fallback_model=fallback, run_id="r1")
    assert out is fallback
