"""LLMFactory.build_cubepi_provider must stamp provider_id onto the cubepi Provider."""

from __future__ import annotations

from cubebox.llm.config import ProviderConfig
from cubebox.llm.factory import LLMFactory


def test_built_provider_carries_provider_id() -> None:
    cfg = ProviderConfig(
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
        api_key="sk-test",
        models=[],
    )
    factory = LLMFactory.__new__(LLMFactory)  # bypass __init__; method doesn't read self
    provider = factory.build_cubepi_provider(cfg, provider_name="anthropic")
    assert provider.provider_id == "anthropic"
    bound = provider.model("claude-3-7-sonnet", max_tokens=1024, temperature=0.5)
    assert bound.spec.provider_id == "anthropic"
