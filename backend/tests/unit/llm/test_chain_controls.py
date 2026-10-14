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


def test_disjoint_mapped_efforts_never_allow_an_arbitrary_effort():
    snap = snapshot()
    provider = snap.providers["p"]
    provider.model_capability_overrides["a"] = {
        "reasoning": {
            "mode_payloads": {"on": {}, "off": {}},
            "effort_path": "reasoning_effort",
            "effort_values": {"low": "low"},
        }
    }
    assert chain_controls(snap, snap.model_presets[0]).efforts == ()
    with pytest.raises(ValueError):
        validate_reasoning_for_chain(
            snap, snap.model_presets[0], ReasoningControl(mode="on", effort="low")
        )


def test_mode_only_controls_do_not_advertise_effort_levels():
    snap = snapshot()
    snap.providers["p"].capability = {"reasoning": {"mode_payloads": {"on": {}, "off": {}}}}
    validate_reasoning_for_chain(snap, snap.model_presets[0], ReasoningControl(mode="on"))
    with pytest.raises(ValueError, match="effort"):
        validate_reasoning_for_chain(
            snap, snap.model_presets[0], ReasoningControl(mode="on", effort="high")
        )


def test_manual_vision_support_remains_subject_to_new_negative_evidence():
    snap = snapshot()
    model = snap.providers["p"].models[0]
    model.configuration_metadata = {
        "origin": "manual",
        "vision_requires_verification": False,
        "vision_verification_required": True,
    }
    model.image_verified = False
    snap.providers["p"].capability["supports_images"] = True
    preset = ModelPreset(key="vision", primary="p/a", fallbacks=(), kind="custom", is_default=True)
    assert chain_controls(snap, preset).input_modalities == ("text",)
    model.image_verified = True
    assert chain_controls(snap, preset).input_modalities == ("image", "text")


def test_current_reasoning_failure_retracts_explicit_controls_but_preserves_default():
    snap = snapshot()
    snap.providers["p"].models[0].reasoning_failed = True
    controls = chain_controls(snap, snap.model_presets[0])
    assert controls.modes == () and controls.efforts == ()
    validate_reasoning_for_chain(snap, snap.model_presets[0], None)
    with pytest.raises(ValueError):
        validate_reasoning_for_chain(
            snap, snap.model_presets[0], ReasoningControl(mode="on", effort="high")
        )
