from cubeplex.llm.discovery_resolver import add_metadata, refresh_eligibility, resolve_models


def test_identity_only_listing_never_fabricates_a_complete_configuration():
    candidates = resolve_models([{"id": "alias/MyModel"}])
    assert candidates[0].model_id == "alias/MyModel"
    assert not candidates[0].eligible
    assert candidates[0].purpose == "unknown"
    assert "context_window" in candidates[0].missing_fields


def test_listing_value_is_used_when_it_only_conflicts_with_catalog():
    from cubeplex.api.schemas.provider_discovery import ImportModelSelection
    from cubeplex.services.provider_discovery import configured_value, review_selection

    candidate = resolve_models(
        [
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "purpose": "chat",
                "context_window": 1_000_000,
                "max_output_tokens": 393216,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    add_metadata(
        candidate,
        {
            "purpose": "chat",
            "context_window": 1_000_000,
            "max_tokens": 32768,
            "input_modalities": ["text"],
        },
        "catalog:deepseek/cn/anthropic-messages",
    )
    output = candidate.fields["max_tokens"]
    assert output.state == "conflict"
    assert output.value == 393216
    assert "max_tokens" not in candidate.missing_fields
    assert candidate.eligible

    reviewed = review_selection(candidate, ImportModelSelection(model_id="deepseek-v4-pro"))
    assert configured_value(reviewed, "max_tokens") == 393216

    chosen = review_selection(
        candidate,
        ImportModelSelection(
            model_id="deepseek-v4-pro",
            resolutions={"max_tokens": "catalog:deepseek/cn/anthropic-messages"},
        ),
    )
    assert configured_value(chosen, "max_tokens") == 32768


def test_models_dev_disagreement_also_defaults_to_the_listing():
    candidate = resolve_models(
        [
            {
                "id": "alias",
                "purpose": "chat",
                "context_window": 8192,
                "max_tokens": 1024,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    add_metadata(
        candidate,
        {
            "purpose": "chat",
            "context_window": 8192,
            "max_tokens": 256,
            "input_modalities": ["text"],
        },
        "models.dev:deepseek",
    )
    assert candidate.fields["max_tokens"].state == "conflict"
    assert candidate.eligible
    assert candidate.fields["max_tokens"].value == 1024


def test_unresolved_import_names_the_missing_fields():
    import pytest

    from cubeplex.api.schemas.provider_discovery import ImportModelSelection
    from cubeplex.services.provider_discovery import review_selection
    from cubeplex.services.provider_discovery_preview import DiscoveryOperationError

    candidate = resolve_models(
        [
            {
                "id": "alias",
                "purpose": "chat",
                "context_window": 8192,
                "max_tokens": 512,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    add_metadata(candidate, {"context_window": 16384}, "instance")
    with pytest.raises(DiscoveryOperationError) as caught:
        review_selection(candidate, ImportModelSelection(model_id="alias"))
    assert caught.value.details["model_id"] == "alias"
    assert caught.value.details["missing_fields"] == ["context_window"]


def test_listing_conflict_with_a_non_catalog_source_stays_unresolved():
    candidate = resolve_models(
        [
            {
                "id": "alias",
                "purpose": "chat",
                "context_window": 8192,
                "max_tokens": 512,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    add_metadata(
        candidate,
        {"context_window": 16384},
        "instance",
    )
    assert candidate.fields["context_window"].state == "conflict"
    assert not candidate.eligible
    assert "context_window" in candidate.missing_fields


def test_duplicate_declared_limits_remain_a_reviewable_conflict():
    rows = [
        {
            "id": "model",
            "purpose": "chat",
            "context_length": 8192,
            "max_tokens": 512,
            "input_modalities": ["text"],
        },
        {"id": "model", "context_length": 32768},
    ]
    candidates = resolve_models(rows)
    assert len(candidates) == 1
    assert candidates[0].fields["context_window"].state == "conflict"
    assert not candidates[0].eligible


def test_template_defaults_cannot_certify_a_chat_model():
    candidate = resolve_models([{"id": "speech-alias"}])[0]
    add_metadata(
        candidate,
        {
            "purpose": "chat",
            "context_window": 8192,
            "max_tokens": 512,
            "input_modalities": ["text", "image"],
            "reasoning": True,
        },
        "client-template",
        state="suggested",
    )
    assert not candidate.eligible
    assert candidate.purpose == "unknown"


def test_known_audio_purpose_cannot_be_selected_as_agent_chat():
    candidate = resolve_models(
        [
            {
                "id": "speech",
                "supportedOutputModalities": ["AUDIO"],
                "context_length": 8192,
                "max_tokens": 512,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    assert candidate.purpose == "audio"
    assert not candidate.eligible


def test_known_video_output_cannot_be_selected_as_agent_chat():
    candidate = resolve_models(
        [
            {
                "id": "clip",
                "supportedOutputModalities": ["TEXT", "VIDEO"],
                "context_length": 8192,
                "max_tokens": 512,
                "input_modalities": ["text"],
            }
        ]
    )[0]
    assert candidate.purpose == "video"
    assert not candidate.eligible


def test_missing_price_differs_from_explicit_zero():
    candidate = resolve_models([{"id": "m"}])[0]
    assert "cost_input" not in candidate.fields
    add_metadata(candidate, {"cost_input": 0}, "instance")
    assert candidate.fields["cost_input"].value == 0
    assert candidate.fields["cost_input"].state == "declared"


def test_purpose_with_invalid_type_is_unresolved():
    candidate = resolve_models([{"id": "m"}])[0]
    from cubeplex.llm.discovery_types import FieldEvidence

    candidate.fields["purpose"] = FieldEvidence(value=["chat"], source="invalid")
    refresh_eligibility(candidate)
    assert candidate.purpose == "unknown"


def test_negative_purpose_cannot_be_overridden_through_a_template_conflict():
    import pytest

    from cubeplex.api.schemas.provider_discovery import ImportModelSelection
    from cubeplex.services.provider_discovery import review_selection
    from cubeplex.services.provider_discovery_preview import DiscoveryOperationError

    model = resolve_models([{"id": "speech", "purpose": "audio"}])[0]
    add_metadata(model, {"purpose": "chat"}, "template", state="suggested")
    with pytest.raises(DiscoveryOperationError, match="chat model"):
        review_selection(
            model,
            ImportModelSelection(
                model_id="speech",
                overrides={
                    "purpose": "chat",
                    "context_window": 4096,
                    "max_tokens": 512,
                    "input_modalities": ["text"],
                },
            ),
        )


def test_explicit_empty_capability_is_not_replaced_by_catalog_defaults():
    from cubeplex.llm.discovery_types import CallingConfiguration
    from cubeplex.services.provider_discovery_preview import resolve_discovery_configuration

    base = {
        "api": "anthropic-messages",
        "base_url": "https://api.anthropic.com",
        "preset_slug": "anthropic/intl/anthropic-messages",
    }
    inherited, _, _ = resolve_discovery_configuration(CallingConfiguration(**base))
    neutral, _, _ = resolve_discovery_configuration(CallingConfiguration(**base, capability={}))
    assert inherited.capability.get("reasoning")
    assert neutral.capability == {}
