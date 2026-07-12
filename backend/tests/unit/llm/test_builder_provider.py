"""builder.build_provider — Provider construction from snapshot.providers[slug]."""

import pytest

from cubeplex.llm.builder import build_provider
from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot


def _snap(**provider_kwargs: object) -> LLMSnapshot:
    return LLMSnapshot(
        providers={"acme": ProviderConfig(api="openai-completions", **provider_kwargs)},
        model_presets=(),
        task_routing={},
    )


def test_build_provider_openai_completions() -> None:
    from cubepi.providers.openai import OpenAIProvider

    p = build_provider(_snap(base_url="https://x", api_key="k"), "acme")
    assert isinstance(p, OpenAIProvider)
    assert p.provider_id == "acme"


def test_build_provider_anthropic_messages_with_cache_policy() -> None:
    from cubepi.providers.anthropic import AnthropicProvider

    from cubeplex.llm.cache_markers import CubeplexCacheMarkerPolicy

    snap = LLMSnapshot(
        providers={
            "anthr": ProviderConfig(
                api="anthropic-messages",
                base_url="https://api.anthropic.com",
                api_key="k",
            ),
        },
        model_presets=(),
        task_routing={},
    )
    p = build_provider(snap, "anthr", cache_policy=CubeplexCacheMarkerPolicy())
    assert isinstance(p, AnthropicProvider)


def test_build_provider_unknown_slug_raises() -> None:
    with pytest.raises(ValueError, match="acme"):
        build_provider(LLMSnapshot(providers={}, model_presets=(), task_routing={}), "acme")
