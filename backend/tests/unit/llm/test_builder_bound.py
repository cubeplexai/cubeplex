"""builder.build_bound_model — bind max_tokens/temperature/reasoning per-leg."""

import pytest

from cubebox.llm.builder import build_bound_model
from cubebox.llm.config import ModelConfig, ProviderConfig
from cubebox.llm.snapshot import LLMSnapshot


def _snap_with_model() -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[
                    ModelConfig(
                        id="m1",
                        name="m1",
                        reasoning=True,
                        context_window=128000,
                        max_tokens=42000,
                    )
                ],
            )
        },
        model_presets=(),
        task_routing={},
    )


def test_build_bound_model_returns_cubepi_boundmodel():
    bm = build_bound_model(_snap_with_model(), "acme/m1")
    from cubepi.providers.base import BoundModel

    assert isinstance(bm, BoundModel)
    assert bm.spec.id == "m1"
    assert bm.spec.provider_id == "acme"


def test_build_bound_model_unknown_provider():
    snap = LLMSnapshot(providers={}, model_presets=(), task_routing={})
    with pytest.raises(ValueError, match="acme"):
        build_bound_model(snap, "acme/m1")


def test_build_bound_model_unknown_model_id():
    with pytest.raises(ValueError, match="m99"):
        build_bound_model(_snap_with_model(), "acme/m99")
