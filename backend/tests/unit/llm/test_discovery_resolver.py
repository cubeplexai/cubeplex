from cubeplex.llm.discovery_resolver import add_metadata, refresh_eligibility, resolve_models


def test_identity_only_listing_never_fabricates_a_complete_configuration():
    candidates = resolve_models([{"id": "alias/MyModel"}])
    assert candidates[0].model_id == "alias/MyModel"
    assert not candidates[0].eligible
    assert candidates[0].purpose == "unknown"
    assert "context_window" in candidates[0].missing_fields


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
