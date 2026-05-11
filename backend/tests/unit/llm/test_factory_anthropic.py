"""Factory branch for Anthropic API providers — unit tests.

We do not call the real network here; we assert that the factory builds
a ChatAnthropic instance with the right kwargs and the correct cubebox
metadata attached for CostMiddleware.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic

from cubebox.llm.config import LLMConfig, ModelConfig, ModelCost, ProviderConfig
from cubebox.llm.factory import LLMFactory


def _build_config(*, reasoning: bool = False, max_tokens: int = 4096) -> LLMConfig:
    return LLMConfig(
        default_model="anthropic-test/claude-test",
        providers={
            "anthropic-test": ProviderConfig(
                api="anthropic",
                base_url="https://example.invalid/anthropic",
                api_key="dummy-key",
                models=[
                    ModelConfig(
                        id="claude-test",
                        name="Claude Test",
                        reasoning=reasoning,
                        context_window=200000,
                        max_tokens=max_tokens,
                        cost=ModelCost(
                            input=3.0,
                            output=15.0,
                            cache_read=0.3,
                            cache_write=3.75,
                        ),
                    )
                ],
            )
        },
    )


@pytest.mark.asyncio
async def test_factory_builds_chat_anthropic_for_anthropic_api() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test")

    assert isinstance(llm, ChatAnthropic)
    assert getattr(llm, "_cubebox_provider", None) == "anthropic-test"
    assert getattr(llm, "_cubebox_model_id", None) == "claude-test"
    assert getattr(llm, "_cubebox_model_cost", None) is not None


@pytest.mark.asyncio
async def test_factory_passes_base_url_and_api_key() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test")

    base_url: Any = getattr(llm, "anthropic_api_url", None) or getattr(llm, "base_url", None)
    assert base_url and "example.invalid" in str(base_url)


@pytest.mark.asyncio
async def test_factory_forwards_temperature() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test", temperature=0.5)
    assert isinstance(llm, ChatAnthropic)
    assert llm.temperature == 0.5


@pytest.mark.asyncio
async def test_factory_forwards_max_tokens() -> None:
    factory = LLMFactory(llm_config=_build_config(max_tokens=8192))
    llm = factory.create("claude-test", provider_name="anthropic-test", max_tokens=2048)
    assert isinstance(llm, ChatAnthropic)
    assert llm.max_tokens == 2048


@pytest.mark.asyncio
async def test_factory_enables_thinking_for_reasoning_model() -> None:
    factory = LLMFactory(llm_config=_build_config(reasoning=True, max_tokens=12000))
    llm = factory.create("claude-test", provider_name="anthropic-test")

    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking is not None
    assert llm.thinking["type"] == "enabled"
    assert llm.thinking["budget_tokens"] == 11999


@pytest.mark.asyncio
async def test_factory_drops_temperature_when_thinking_enabled() -> None:
    factory = LLMFactory(llm_config=_build_config(reasoning=True, max_tokens=12000))
    llm = factory.create("claude-test", provider_name="anthropic-test", temperature=0.7)
    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking is not None
    # Anthropic requires temperature unset (defaults to 1) with thinking
    assert llm.temperature is None or llm.temperature == 1


@pytest.mark.asyncio
async def test_factory_skips_thinking_when_max_tokens_too_small() -> None:
    factory = LLMFactory(llm_config=_build_config(reasoning=True, max_tokens=1024))
    llm = factory.create("claude-test", provider_name="anthropic-test")

    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking is None


@pytest.mark.asyncio
async def test_factory_reasoning_config_overrides_auto_thinking() -> None:
    factory = LLMFactory(llm_config=_build_config(reasoning=True))
    custom = {"type": "enabled", "budget_tokens": 500}
    llm = factory.create("claude-test", provider_name="anthropic-test", reasoning_config=custom)
    assert isinstance(llm, ChatAnthropic)
    assert llm.thinking == custom


@pytest.mark.asyncio
async def test_factory_wraps_anthropic_with_cache_markers() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test")

    agenerate = llm._agenerate  # type: ignore[attr-defined]
    assert agenerate.__name__ == "patched_agenerate", (
        "factory must wrap Anthropic models with _wrap_with_cache_markers"
    )
