"""Builder plumbs capability + model_capability_overrides into cubepi providers.

Task 3 (LLM provider platform, slice 2). Verifies that DB JSON capability columns
flow through ProviderConfig -> build_provider -> cubepi provider classes as
typed CapabilityDescriptor objects.
"""

from typing import Any

from cubebox.llm.builder import build_provider
from cubebox.llm.config import ProviderConfig
from cubebox.llm.snapshot import LLMSnapshot


def _bare_provider_config(
    api: str,
    *,
    capability: dict[str, Any] | None = None,
    model_capability_overrides: dict[str, Any] | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        api=api,
        base_url="https://example.test",
        api_key="sk-test",
        capability=capability or {},
        model_capability_overrides=model_capability_overrides or {},
    )


def _build(cfg: ProviderConfig, *, slug: str = "acme") -> Any:
    snap = LLMSnapshot(providers={slug: cfg}, model_presets=(), task_routing={})
    return build_provider(snap, slug)


def test_legacy_no_capability_keeps_cap_inactive() -> None:
    """OpenAI provider with empty capability stays behavior-identical (_cap_active False)."""
    cfg = _bare_provider_config("openai-completions")
    provider = _build(cfg)
    assert provider._cap_active is False


def test_with_capability_activates_and_carries_payload() -> None:
    """A non-empty capability dict becomes a typed descriptor and activates the path."""
    cap = {"reasoning_off_payload": {"reasoning_effort": "none"}}
    cfg = _bare_provider_config("openai-completions", capability=cap)
    provider = _build(cfg)
    assert provider._cap_active is True
    assert provider._capability.reasoning_off_payload == {"reasoning_effort": "none"}


def test_model_capability_overrides_are_typed() -> None:
    """Per-model override dicts become typed descriptors keyed by model id."""
    overrides = {
        "gpt-5": {"reasoning_off_payload": {"reasoning_effort": "minimal"}},
    }
    cfg = _bare_provider_config("openai-completions", model_capability_overrides=overrides)
    provider = _build(cfg)
    assert provider._cap_active is True
    assert "gpt-5" in provider._model_overrides
    assert provider._model_overrides["gpt-5"].reasoning_off_payload == {
        "reasoning_effort": "minimal"
    }


def test_anthropic_receives_capability() -> None:
    """Anthropic provider gets the typed capability (no _cap_active gate on this class)."""
    cap = {"reasoning_on_payload": {"thinking": {"type": "enabled"}}}
    cfg = _bare_provider_config("anthropic-messages", capability=cap)
    provider = _build(cfg)
    assert provider._capability.reasoning_on_payload == {"thinking": {"type": "enabled"}}


def test_openai_responses_activates_with_capability() -> None:
    """openai-responses provider activates the capability path when a capability is set."""
    cap = {"reasoning_on_payload": {"reasoning": {"effort": "high"}}}
    cfg = _bare_provider_config("openai-responses", capability=cap)
    provider = _build(cfg)
    assert provider._cap_active is True
    assert provider._capability.reasoning_on_payload == {"reasoning": {"effort": "high"}}
