"""builder.build_provider stamps provider_id from snapshot key (formerly factory)."""

from __future__ import annotations

from cubebox.llm.builder import build_provider
from cubebox.llm.config import ProviderConfig
from cubebox.llm.snapshot import LLMSnapshot


def test_built_provider_carries_provider_id() -> None:
    cfg = ProviderConfig(
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
        api_key="sk-test",
        models=[],
    )
    snap = LLMSnapshot(providers={"anthropic": cfg}, model_presets=(), task_routing={})
    provider = build_provider(snap, "anthropic")
    assert provider.provider_id == "anthropic"
    bound = provider.model("claude-3-7-sonnet", max_tokens=1024, temperature=0.5)
    assert bound.spec.provider_id == "anthropic"
