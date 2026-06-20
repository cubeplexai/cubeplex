"""Pure builders — emit cubepi Provider / BoundModel objects from a snapshot.

No DB. No cubebox.config. The chain wrapper in build_chain_model() is
added in Task A7 (chain length 1 only for PR 1) and Task B1 (length >1).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from cubepi.providers.base import ThinkingLevel

from cubebox.llm.resolver import parse_model_ref
from cubebox.llm.snapshot import LLMSnapshot, ModelPreset

if TYPE_CHECKING:
    from cubepi.providers.anthropic import CacheMarkerPolicy


OnFailoverCb = Callable[[Any, Any, BaseException | str], Awaitable[None] | None]


def build_provider(
    snap: LLMSnapshot,
    slug: str,
    *,
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    """Build a cubepi Provider for snap.providers[slug] based on its api type."""
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")

    from cubepi.providers.capability import CapabilityDescriptor

    cap_dict = cfg.capability or {}
    capability = CapabilityDescriptor.model_validate(cap_dict) if cap_dict else None

    overrides_raw = cfg.model_capability_overrides or {}
    overrides = {
        mid: CapabilityDescriptor.model_validate(d) for mid, d in overrides_raw.items()
    } or None

    api = cfg.api
    if api == "anthropic-messages":
        from cubepi.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            cache_policy=cache_policy,
            capability=capability,
            model_capability_overrides=overrides,
        )
    if api == "openai-completions":
        from cubepi.providers.openai import OpenAIProvider

        return OpenAIProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            extra_body=cfg.extra_body or None,
            extra_headers=cfg.extra_headers or None,
            capability=capability,
            model_capability_overrides=overrides,
        )
    if api == "openai-responses":
        from cubepi.providers.openai_responses import OpenAIResponsesProvider

        return OpenAIResponsesProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            capability=capability,
            model_capability_overrides=overrides,
        )

    raise ValueError(f"unsupported api for cubepi provider: {api!r}")


def build_bound_model(
    snap: LLMSnapshot,
    ref: str,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    """Build a cubepi BoundModel for `ref`, binding max_tokens / reasoning.

    `thinking` is reserved for chain wrapping (Task B1) and Agent.prompt()
    binding (Task C2); cubepi applies it at runtime, not at BoundModel build
    time. Kept on the signature so callers stay stable across A7/B1/C2.
    """
    slug, model_id = parse_model_ref(ref)
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")
    model_cfg = next((m for m in cfg.models if m.id == model_id), None)
    if model_cfg is None:
        raise ValueError(f"model {model_id!r} not in provider {slug!r}")
    provider = build_provider(snap, slug, cache_policy=cache_policy)
    return provider.model(
        model_id,
        reasoning=model_cfg.reasoning,
        max_tokens=model_cfg.max_tokens or 32000,
        temperature=0.7,
    )


def build_chain_model(
    snap: LLMSnapshot,
    preset: ModelPreset,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy_factory: Callable[[str], "CacheMarkerPolicy | None"] | None = None,
    on_failover: OnFailoverCb | None = None,
) -> Any:
    """chain length 1 → BoundModel; >1 → FallbackBoundModel."""
    if len(preset.chain) == 0:
        raise ValueError(f"preset {preset.key!r} has empty chain")

    if len(preset.chain) == 1:
        ref = preset.chain[0]
        slug, _ = parse_model_ref(ref)
        policy = cache_policy_factory(slug) if cache_policy_factory else None
        return build_bound_model(snap, ref, thinking=thinking, cache_policy=policy)

    from cubepi.providers.fallback import FallbackBoundModel

    bounds = []
    for ref in preset.chain:
        slug, _ = parse_model_ref(ref)
        policy = cache_policy_factory(slug) if cache_policy_factory else None
        bounds.append(build_bound_model(snap, ref, thinking=thinking, cache_policy=policy))

    return FallbackBoundModel(chain=tuple(bounds), on_failover=on_failover)
