"""Unit tests for LLMFactory.build_cubepi_provider (M0.6)."""

import pytest

from cubebox.llm.config import LLMConfig, ProviderConfig
from cubebox.llm.factory import LLMFactory


def _mk_factory(named_configs: dict[str, ProviderConfig]) -> LLMFactory:
    return LLMFactory(
        llm_config=LLMConfig(
            default_model="anthropic/claude-sonnet-4",
            providers=named_configs,
        )
    )


def test_build_cubepi_provider_routes_anthropic() -> None:
    from cubepi.providers.anthropic import AnthropicProvider

    factory = _mk_factory(
        {
            "anthropic": ProviderConfig(
                api="anthropic-messages",
                base_url="https://api.anthropic.com",
                api_key="sk-test",
            ),
        }
    )
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["anthropic"], provider_name="anthropic"
    )
    assert isinstance(provider, AnthropicProvider)


def test_build_cubepi_provider_routes_openai_completions() -> None:
    from cubepi.providers.openai import OpenAIProvider

    factory = _mk_factory(
        {
            "deepseek": ProviderConfig(
                api="openai-completions",
                base_url="https://api.deepseek.com",
                api_key="sk-test",
            ),
        }
    )
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["deepseek"], provider_name="deepseek"
    )
    assert isinstance(provider, OpenAIProvider)


def test_build_cubepi_provider_routes_openai_responses() -> None:
    from cubepi.providers.openai_responses import OpenAIResponsesProvider

    factory = _mk_factory(
        {
            "oai-responses": ProviderConfig(
                api="openai-responses",
                base_url="https://api.openai.com",
                api_key="sk-test",
            ),
        }
    )
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["oai-responses"], provider_name="oai-responses"
    )
    assert isinstance(provider, OpenAIResponsesProvider)


def test_build_cubepi_provider_unknown_api_raises() -> None:
    # Manually override the api field after construction to test the factory's defense.
    factory = _mk_factory(
        {
            "weird": ProviderConfig(
                api="anthropic-messages",  # valid; we'll override below
                base_url="https://x.com",
                api_key="sk",
            ),
        }
    )
    cfg = factory.llm_config.providers["weird"]
    object.__setattr__(cfg, "api", "some-unknown-api")
    with pytest.raises(ValueError, match="unsupported api"):
        factory.build_cubepi_provider(cfg, provider_name="weird")


def test_build_cubepi_provider_anthropic_accepts_cache_policy() -> None:
    """Factory passes cache_policy through to AnthropicProvider."""
    from cubepi.providers.anthropic import DefaultCacheMarkerPolicy

    factory = _mk_factory(
        {
            "anthropic": ProviderConfig(
                api="anthropic-messages",
                base_url="https://api.anthropic.com",
                api_key="sk-test",
            ),
        }
    )
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["anthropic"],
        provider_name="anthropic",
        cache_policy=None,
    )
    # When cache_policy=None, AnthropicProvider falls back to DefaultCacheMarkerPolicy
    assert isinstance(provider._cache_policy, DefaultCacheMarkerPolicy)
