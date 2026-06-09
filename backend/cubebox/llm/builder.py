"""Pure builders — emit cubepi Provider / BoundModel objects from a snapshot.

No DB. No cubebox.config. The chain wrapper in build_chain_model() is
added in Task A7 (chain length 1 only for PR 1) and Task B1 (length >1).
"""

from typing import TYPE_CHECKING, Any

from cubebox.llm.snapshot import LLMSnapshot

if TYPE_CHECKING:
    from cubepi.providers.anthropic import CacheMarkerPolicy


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
