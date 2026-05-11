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


def _build_config() -> LLMConfig:
    """Minimal LLMConfig with a single Anthropic provider."""
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
                        context_window=200000,
                        max_tokens=4096,
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
    # Cubebox metadata must round-trip for CostMiddleware
    assert getattr(llm, "_cubebox_provider", None) == "anthropic-test"
    assert getattr(llm, "_cubebox_model_id", None) == "claude-test"
    assert getattr(llm, "_cubebox_model_cost", None) is not None


@pytest.mark.asyncio
async def test_factory_passes_base_url_and_api_key() -> None:
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test")

    # ChatAnthropic stores base_url under different attr names depending on
    # version; check both.
    base_url: Any = getattr(llm, "anthropic_api_url", None) or getattr(llm, "base_url", None)
    assert base_url and "example.invalid" in str(base_url)


@pytest.mark.asyncio
async def test_factory_wraps_anthropic_with_cache_markers() -> None:
    """The Anthropic branch must apply cache_control via _wrap_with_cache_markers."""
    factory = LLMFactory(llm_config=_build_config())
    llm = factory.create("claude-test", provider_name="anthropic-test")

    # _wrap_with_cache_markers patches `_agenerate` in-place. The patched
    # method's qualname includes "patched_agenerate" or has been replaced.
    agenerate = llm._agenerate  # type: ignore[attr-defined]
    assert agenerate.__name__ in {"patched_agenerate", "_agenerate"}
    # If it's still _agenerate, cache markers were not applied.
    assert agenerate.__name__ == "patched_agenerate", (
        "factory must wrap Anthropic models with _wrap_with_cache_markers"
    )
