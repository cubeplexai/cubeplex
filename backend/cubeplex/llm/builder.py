"""Pure builders — emit cubeloop Provider / BoundModel objects from a snapshot.

No DB. No cubeplex.config. The chain wrapper in build_chain_model() is
added in Task A7 (chain length 1 only for PR 1) and Task B1 (length >1).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from cubeloop.providers.base import ReasoningControl

from cubeplex.llm.request_parameters import validate_request_extensions, validate_request_headers
from cubeplex.llm.resolver import parse_model_ref
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset

if TYPE_CHECKING:
    from cubeloop.providers.anthropic import CacheMarkerPolicy


OnFailoverCb = Callable[[Any, Any, BaseException | str], Awaitable[None] | None]
OnRetryCb = Callable[[Any, BaseException, int, float], Awaitable[None] | None]


def build_provider(
    snap: LLMSnapshot,
    slug: str,
    *,
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    """Build a cubeloop Provider for snap.providers[slug] based on its api type."""
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")

    from cubeloop.providers.capability import CapabilityDescriptor

    cap_dict = cfg.capability or {}
    capability = CapabilityDescriptor.model_validate(cap_dict)

    overrides_raw = cfg.model_capability_overrides or {}
    overrides = {
        mid: CapabilityDescriptor.model_validate(d) for mid, d in overrides_raw.items()
    } or None

    api = cfg.api
    if cfg.provider_db_id and cfg.auth_type != "none" and not cfg.api_key:
        raise ValueError("The saved provider credential is unavailable")
    api_key = "cubeplex-no-auth" if cfg.auth_type == "none" else cfg.api_key

    def configure_auth(provider: Any) -> Any:
        if cfg.auth_type == "none":
            from cubeplex.llm.request_parameters import NoAuthentication

            provider._client._client.auth = NoAuthentication()
        return provider

    validate_request_extensions(api, cfg.extra_body or {}, cap_dict)
    validate_request_headers(api, cfg.extra_headers)
    if api == "anthropic-messages":
        from cubeplex.llm.anthropic_provider import AnthropicProvider

        return configure_auth(
            AnthropicProvider(
                provider_id=slug,
                api_key=api_key,
                base_url=cfg.base_url or None,
                cache_policy=cache_policy,
                capability=capability,
                model_capability_overrides=overrides,
            )
        )
    if api == "openai-completions":
        from cubeloop.providers.openai import OpenAIProvider

        return configure_auth(
            OpenAIProvider(
                provider_id=slug,
                api_key=api_key,
                base_url=cfg.base_url,
                extra_body=cfg.extra_body or None,
                extra_headers=cfg.extra_headers or None,
                capability=capability,
                model_capability_overrides=overrides,
            )
        )
    if api == "openai-responses":
        from cubeloop.providers.openai_responses import OpenAIResponsesProvider

        return configure_auth(
            OpenAIResponsesProvider(
                provider_id=slug,
                api_key=api_key,
                base_url=cfg.base_url,
                capability=capability,
                model_capability_overrides=overrides,
            )
        )

    raise ValueError(f"unsupported api for cubeloop provider: {api!r}")


def build_bound_model(
    snap: LLMSnapshot,
    ref: str,
    *,
    cache_policy: "CacheMarkerPolicy | None" = None,
    reasoning: ReasoningControl | None = None,
) -> Any:
    """Build a cubeloop BoundModel for `ref`, binding model metadata."""
    slug, model_id = parse_model_ref(ref)
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")
    model_cfg = next((m for m in cfg.models if m.id == model_id), None)
    if model_cfg is None:
        raise ValueError(f"model {model_id!r} not in provider {slug!r}")
    from dataclasses import replace

    from cubeplex.llm.request_parameters import prepare_provider_for_call

    merged = cfg.model_copy(
        update={
            "extra_body": {**cfg.extra_body, **model_cfg.extra_body},
            "extra_headers": {**cfg.extra_headers, **model_cfg.extra_headers},
        }
    )
    effective = merged.model_capability_overrides.get(model_id, merged.capability)
    validate_request_extensions(cfg.api, merged.extra_body, effective)
    providers = {**snap.providers, slug: merged}
    provider = build_provider(replace(snap, providers=providers), slug, cache_policy=cache_policy)
    provider = prepare_provider_for_call(provider, model_id, reasoning)
    if (
        cfg.provider_db_id
        and model_cfg.model_db_id
        and model_cfg.configuration_fingerprint
        and cfg.configuration_fingerprint
    ):
        from cubeplex.llm.runtime_writeback import (
            RuntimeObservation,
            schedule_runtime_status_writeback,
        )

        observation = RuntimeObservation(
            org_id=cfg.org_id,
            provider_id=cfg.provider_db_id,
            model_db_id=model_cfg.model_db_id,
            configuration_fingerprint=model_cfg.configuration_fingerprint,
            provider_fingerprint=cfg.configuration_fingerprint,
            provider_evidence_revision=cfg.evidence_revision,
            model_evidence_revision=model_cfg.evidence_revision,
            provider_probe_generation=cfg.probe_generation,
            model_probe_generation=model_cfg.probe_generation,
        )

        def record_response(
            _body: dict[str, Any] | None, _model: Any, exc: BaseException | None
        ) -> None:
            schedule_runtime_status_writeback(observation=observation, exc=exc)

        provider.subscribe_response(record_response)
    return provider.model(
        model_id,
        reasoning=model_cfg.reasoning,
        context_window=model_cfg.context_window,
        max_tokens=model_cfg.max_tokens,
        temperature=0.7,
    )


def build_chain_model(
    snap: LLMSnapshot,
    preset: ModelPreset,
    *,
    cache_policy_factory: Callable[[str], "CacheMarkerPolicy | None"] | None = None,
    on_failover: OnFailoverCb | None = None,
    on_retry: OnRetryCb | None = None,
    reasoning: ReasoningControl | None = None,
) -> Any:
    """chain length 1 → BoundModel; >1 → FallbackBoundModel."""
    if len(preset.chain) == 0:
        raise ValueError(f"preset {preset.key!r} has empty chain")

    if len(preset.chain) == 1:
        ref = preset.chain[0]
        slug, _ = parse_model_ref(ref)
        policy = cache_policy_factory(slug) if cache_policy_factory else None
        return build_bound_model(snap, ref, cache_policy=policy, reasoning=reasoning)

    from cubeloop.providers.fallback import FallbackBoundModel

    bounds = []
    for ref in preset.chain:
        slug, _ = parse_model_ref(ref)
        policy = cache_policy_factory(slug) if cache_policy_factory else None
        bounds.append(build_bound_model(snap, ref, cache_policy=policy, reasoning=reasoning))

    return FallbackBoundModel(chain=tuple(bounds), on_failover=on_failover, on_retry=on_retry)
