import json

from cubeplex.llm.discovery_resolver import resolve_models
from cubeplex.llm.discovery_types import CallingConfiguration
from cubeplex.llm.models_dev import (
    apply_models_dev,
    lookup_model,
    reduce_provider_catalog,
    snapshot_path,
    snapshot_source,
)

_CHAT = {
    "context": 128000,
    "output": 4096,
    "input": ["text"],
    "output_modalities": ["text"],
}


def test_reducer_keeps_exact_ids_and_only_positive_finite_costs():
    reduced = reduce_provider_catalog(
        {
            "openai": {
                "models": {
                    "priced": {
                        "limit": {"context": 8, "output": 4},
                        "modalities": {"input": ["Text", "TEXT"], "output": ["text"]},
                        "reasoning": True,
                        "reasoning_options": [
                            {"type": "effort", "values": ["low", "ultra", "high"]}
                        ],
                        "cost": {
                            "input": 0,
                            "output": 1.5,
                            "cache_read": float("nan"),
                            "cache_write": True,
                        },
                    },
                    "bare": {"name": "Bare"},
                }
            },
            "skip": [],
        }
    )
    record = reduced["openai"]["priced"]
    assert record["input"] == ["text"]
    assert record["output_modalities"] == ["text"]
    assert record["efforts"] == ["low", "high"]
    assert record["cost_output"] == 1.5
    assert "cost_input" not in record
    assert "cost_cache_read" not in record
    assert "cost_cache_write" not in record
    assert reduced["openai"]["bare"] == {}
    assert "skip" not in reduced


def test_official_record_wins_and_a_zero_price_stays_unknown():
    index = {
        "shared": [
            (
                "openrouter",
                {**_CHAT, "context": 1, "cost_input": 9},
            ),
            ("anthropic", {**_CHAT, "context": 200000, "cost_input": 0}),
        ]
    }
    model = resolve_models([{"id": "shared"}])[0]
    apply_models_dev(model, CallingConfiguration(base_url="https://endpoint.test/v1"), index)
    assert model.fields["context_window"].source == "models.dev:anthropic"
    assert model.fields["context_window"].value == 200000
    assert model.fields["context_window"].state == "declared"
    assert "cost_input" not in model.fields
    assert model.eligible


def test_suffix_is_removed_once_and_only_after_an_exact_miss():
    base = {"named": [("openai", _CHAT)]}
    assert lookup_model("named-thinking", base) == ("openai", _CHAT)
    assert lookup_model("named:high", base) == ("openai", _CHAT)
    shorter = {**_CHAT, "context": 1}
    xhigh = {"named": [("xai", _CHAT)], "named-x": [("openai", shorter)]}
    assert lookup_model("named-xhigh", xhigh) == ("xai", _CHAT)
    blocked = {
        "gemini-3.8-flash-high": [("google", {})],
        "gemini-3.8-flash": [("google", _CHAT)],
    }
    assert lookup_model("gemini-3.8-flash-high", blocked) == ("google", {})
    held = resolve_models([{"id": "gemini-3.8-flash-high"}])[0]
    apply_models_dev(held, CallingConfiguration(base_url="https://endpoint.test/v1"), blocked)
    assert "context_window" not in held.fields
    assert not held.eligible
    assert lookup_model("named-thinking-high", base) is None
    assert lookup_model("named-thinking-high", {"named-thinking": [("openai", _CHAT)]}) == (
        "openai",
        _CHAT,
    )
    assert lookup_model("grok-3-mini-fast", {"grok-3-mini": [("xai", _CHAT)]}) is None
    assert lookup_model("grok-3-mini-preview", {"grok-3-mini": [("xai", _CHAT)]}) is None
    dotted = {"grok-imagine-image-2-0": [("xai", _CHAT)]}
    assert lookup_model("grok-imagine-image-2.0", dotted) is None


def test_non_text_output_cannot_become_an_agent_chat_model():
    config = CallingConfiguration(base_url="https://endpoint.test/v1", api="openai-responses")
    image = resolve_models([{"id": "picture"}])[0]
    apply_models_dev(
        image,
        config,
        {"picture": [("openai", {**_CHAT, "output_modalities": ["image"]})]},
    )
    assert image.purpose == "image" and not image.eligible
    video = resolve_models([{"id": "clip"}])[0]
    apply_models_dev(
        video,
        config,
        {"clip": [("openai", {**_CHAT, "output_modalities": ["text", "video"]})]},
    )
    assert video.purpose == "video" and not video.eligible


def test_standard_efforts_fill_a_responses_capability():
    model = resolve_models([{"id": "gpt-x"}])[0]
    apply_models_dev(
        model,
        CallingConfiguration(base_url="https://endpoint.test/v1", api="openai-responses"),
        {"gpt-x": [("openai", {**_CHAT, "reasoning": True, "efforts": ["low", "high"]})]},
    )
    capability = model.fields["capability"].value
    assert isinstance(capability, dict)
    reasoning = capability["reasoning"]
    assert reasoning["effort_path"] == "reasoning.effort"
    assert reasoning["effort_values"] == {"low": "low", "high": "high"}
    assert model.fields["reasoning"].value is True


def test_completions_effort_path_and_aggregator_tie_break():
    chosen = lookup_model(
        "shared",
        {"shared": [("openrouter", _CHAT), ("together", _CHAT), ("google", _CHAT)]},
    )
    assert chosen is not None and chosen[0] == "google"
    aggregators = lookup_model("shared", {"shared": [("openrouter", _CHAT), ("together", _CHAT)]})
    assert aggregators is not None and aggregators[0] == "openrouter"
    model = resolve_models([{"id": "gpt-x"}])[0]
    apply_models_dev(
        model,
        CallingConfiguration(base_url="https://endpoint.test/v1"),
        {"gpt-x": [("openai", {**_CHAT, "efforts": ["medium"]})]},
    )
    capability = model.fields["capability"].value
    assert isinstance(capability, dict)
    assert capability["reasoning"]["effort_path"] == "reasoning_effort"


def test_committed_snapshot_is_a_reduced_models_dev_catalog():
    path = snapshot_path()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["source"] == snapshot_source()
    assert document["license"] == "MIT"
    assert document["copyright"] == "Copyright (c) 2025 models.dev"
    providers = document["providers"]
    assert isinstance(providers, dict) and providers
    allowed = {
        "context",
        "output",
        "input",
        "output_modalities",
        "reasoning",
        "efforts",
        "cost_input",
        "cost_output",
        "cost_cache_read",
        "cost_cache_write",
    }
    for models in providers.values():
        assert isinstance(models, dict) and models
        for record in models.values():
            assert isinstance(record, dict)
            assert set(record).issubset(allowed)
    license_text = (path.parent / "MODELS_DEV_LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2025 models.dev" in license_text
