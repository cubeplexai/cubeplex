import pytest
from cubeloop.providers.base import ReasoningControl

from cubeplex.llm.config import ModelConfig, ProviderConfig
from cubeplex.llm.controls import chain_controls, reasoning_support, validate_reasoning_for_chain
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset


def snapshot():
    preset = ModelPreset(
        key="team", primary="p/a", fallbacks=("p/b",), kind="custom", is_default=True
    )
    cap = {
        "reasoning": {
            "mode_payloads": {"on": {}, "off": {}},
            "effort_path": "reasoning_effort",
            "effort_values": {"high": "high"},
        }
    }
    models = [
        ModelConfig(
            id=id, name=id, context_window=4096, max_tokens=512, reasoning=True, input=inputs
        )
        for id, inputs in [("a", ["text", "image"]), ("b", ["text"])]
    ]
    return LLMSnapshot(
        providers={
            "p": ProviderConfig(base_url="http://localhost/v1", capability=cap, models=models)
        },
        model_presets=(preset,),
        task_routing={},
    )


def test_fallback_controls_and_attachments_require_every_member():
    snap = snapshot()
    assert chain_controls(snap, snap.model_presets[0]).input_modalities == ("text",)
    assert chain_controls(snap, snap.model_presets[0]).efforts == ("high",)
    validate_reasoning_for_chain(snap, snap.model_presets[0], None)
    with pytest.raises(ValueError, match="effort"):
        validate_reasoning_for_chain(snap, snap.model_presets[0], ReasoningControl(mode="on"))
    validate_reasoning_for_chain(
        snap, snap.model_presets[0], ReasoningControl(mode="on", effort="high")
    )


def test_unknown_imported_reasoning_does_not_become_a_negative_declaration():
    model = snapshot().providers["p"].models[0]
    model.configuration_metadata = {"origin": "discovery", "fields": {}}
    model.reasoning = False
    assert reasoning_support(model) is None
    model.configuration_metadata["fields"]["reasoning"] = {"state": "confirmed", "value": False}
    assert reasoning_support(model) is False
