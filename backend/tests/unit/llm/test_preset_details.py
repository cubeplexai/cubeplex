"""Unit tests for workspace preset primary → model detail resolution."""

from cubeplex.llm.config import ModelConfig, ModelCost, ProviderConfig
from cubeplex.llm.preset_details import detail_fields, find_model, split_primary


def _provider(*models: ModelConfig) -> ProviderConfig:
    return ProviderConfig(
        base_url="https://example.com/v1",
        models=list(models),
    )


def _model(
    mid: str,
    *,
    name: str | None = None,
    context_window: int = 128_000,
    reasoning: bool = False,
    input: list[str] | None = None,
) -> ModelConfig:
    return ModelConfig(
        id=mid,
        name=name or mid,
        context_window=context_window,
        max_tokens=4096,
        reasoning=reasoning,
        input=input or ["text"],
        cost=ModelCost(input=0, output=0),
    )


def test_split_primary_first_slash_only() -> None:
    assert split_primary("acme/qwen/v1") == ("acme", "qwen/v1")
    assert split_primary("openai/gpt-4o") == ("openai", "gpt-4o")


def test_split_primary_no_slash() -> None:
    assert split_primary("bare-slug") == ("bare-slug", None)


def test_split_primary_empty() -> None:
    assert split_primary("") == (None, None)


def test_find_model_and_detail_fields() -> None:
    providers = {
        "anthropic": _provider(
            _model(
                "claude-opus-4-7",
                name="Claude Opus 4.7",
                context_window=1_000_000,
                reasoning=True,
                input=["text", "image"],
            )
        ),
        "openai": _provider(_model("gpt-5", name="GPT-5", context_window=200_000, reasoning=False)),
    }

    a = detail_fields(providers, "anthropic/claude-opus-4-7")
    assert a.provider_slug == "anthropic"
    assert a.model_id == "claude-opus-4-7"
    assert a.model_display_name == "Claude Opus 4.7"
    assert a.context_window == 1_000_000
    assert a.reasoning is True
    assert a.input_modalities == ["text", "image"]

    b = detail_fields(providers, "openai/gpt-5")
    assert b.provider_slug == "openai"
    assert b.model_display_name == "GPT-5"
    # No cross-wiring from anthropic
    assert b.context_window == 200_000
    assert b.reasoning is False


def test_detail_fields_missing_model() -> None:
    providers = {"acme": _provider(_model("exists"))}
    d = detail_fields(providers, "acme/missing")
    assert d.provider_slug == "acme"
    assert d.model_id == "missing"
    assert d.model_display_name is None
    assert d.context_window is None


def test_detail_fields_unknown_provider() -> None:
    d = detail_fields({}, "ghost/m1")
    assert d.provider_slug == "ghost"
    assert d.model_id == "m1"
    assert d.reasoning is None


def test_find_model_nested_id() -> None:
    providers = {"v": _provider(_model("org/model/v1", name="Nested"))}
    mc = find_model(providers, "v", "org/model/v1")
    assert mc is not None
    assert mc.name == "Nested"
    assert detail_fields(providers, "v/org/model/v1").model_display_name == "Nested"
