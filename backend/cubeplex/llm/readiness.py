"""Pure helpers for the per-model `readiness` enum (spec §4.1).

Readiness combines one cheap provider-level liveness status with each model's
own probe status into a single value the UI reads verbatim (it never
re-derives). The model picker (later slice) only looks at `readiness`.

Two never-tested decisions are baked in here:

- ``liveness_status is None`` (never probed) is NOT a failure. A seeded/legacy
  provider is presumed reachable; the liveness probe only DOWNGRADES on an
  explicit ``"fail"``. So only an explicit ``"fail"`` yields ``provider_error``.
- ``model_test_status is None`` (never probed) → ``"ready"``. Absence of a
  failing test is not a failure: wizard-created models are gated on a passing
  test, so only seeded/legacy models lack probe data, and we must not disable
  an org's actual working models.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cubeloop.providers.capability import CapabilityDescriptor

from cubeplex.models.provider import Model, Provider

# §4.1 readiness enum values (string literals, server-derived).
READY = "ready"
DEGRADED = "degraded"
STALE = "stale"
MODEL_ERROR = "model_error"
UNAVAILABLE = "unavailable"
PROVIDER_ERROR = "provider_error"
AUTH_ERROR = "auth_error"


def derive_readiness(
    *,
    liveness_status: str | None,
    model_test_status: str | None,
    capability_changed_since_test: bool,
) -> str:
    """Map (provider liveness, model test, capability-edit) → readiness enum.

    Precedence (spec §4.1), highest first:
    1. provider liveness == "auth_error"       -> "auth_error" (all models)
    2. provider liveness == "fail"             -> "provider_error" (all models)
    3. model_test_status == "unavailable"      -> "unavailable"
    4. model_test_status == "fail"             -> "model_error"
    5. capability_changed_since_test           -> "stale"
    6. model_test_status == "warn"             -> "degraded"
    7. otherwise                               -> "ready"

    ``auth_error`` and ``fail`` are both provider-grain (they black out every
    model), but split so the UI can say "fix the key" vs "endpoint is down".

    Never-probed inputs are presumed healthy: ``liveness_status is None`` is
    treated as "not failed" and ``model_test_status is None`` falls through to
    "ready". See module docstring for the rationale.
    """
    if liveness_status == "auth_error":
        return AUTH_ERROR
    if liveness_status == "fail":
        return PROVIDER_ERROR
    if model_test_status == "unavailable":
        return UNAVAILABLE
    if model_test_status == "fail":
        return MODEL_ERROR
    if capability_changed_since_test:
        return STALE
    if model_test_status == "warn":
        return DEGRADED
    return READY


def capability_fingerprint(
    capability: dict[str, Any],
    model_capability_overrides: dict[str, Any],
) -> str:
    """Stable hash of the capability inputs that gate `stale` readiness.

    Persisted into a model's ``last_test_summary`` by the probe (Task 10) so a
    later read can detect a capability edit since the last test. Kept here so
    the probe and the serializer agree on the exact algorithm.
    """
    payload = json.dumps(
        {"capability": capability, "model_capability_overrides": model_capability_overrides},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def provider_configuration_fingerprint(provider: Provider, credential_revision: int) -> str:
    return _hash(
        {
            "api": provider.provider_type,
            "base_url": provider.base_url.rstrip("/"),
            "auth_type": provider.auth_type,
            "credential_id": provider.credential_id,
            "credential_revision": credential_revision,
            "extra_body": provider.extra_body or {},
            "extra_headers": provider.extra_headers or {},
        }
    )


def configuration_fingerprint(
    provider: Provider, model: Model, credential_revision: int = 1
) -> str:
    overrides = provider.model_capability_overrides or {}
    capability = overrides.get(model.model_id, provider.capability or {})
    effective = CapabilityDescriptor.model_validate(capability).model_dump(mode="json")
    return _hash(
        {
            "provider": provider_configuration_fingerprint(provider, credential_revision),
            "model_id": model.model_id,
            "capability": effective,
            "context_window": model.context_window,
            "max_tokens": model.max_tokens,
            "reasoning": model.reasoning,
            "input_modalities": sorted(model.input_modalities),
            "extra_body": {**(provider.extra_body or {}), **(model.extra_body or {})},
            "extra_headers": {**(provider.extra_headers or {}), **(model.extra_headers or {})},
        }
    )


def verification_pending(provider: Provider, model: Model, credential_revision: int = 1) -> bool:
    summary = model.last_test_summary or {}
    stored = summary.get("configuration_fingerprint")
    if stored is None:
        return (
            model.last_test_status is not None
            or model.configuration_metadata.get("origin") == "discovery"
        )
    return (
        stored != configuration_fingerprint(provider, model, credential_revision)
        or summary.get("probe_generation") != model.probe_generation
        or summary.get("evidence_revision") != model.evidence_revision
    )


def model_activation_blocker(
    provider: Provider, model: Model, credential_revision: int = 1
) -> str | None:
    summary = model.last_test_summary or {}
    if verification_pending(provider, model, credential_revision):
        return "verification_pending"
    if model.last_test_status not in {"ok", "warn"}:
        return "model_test_required"
    if (
        summary.get("probe_generation") != model.probe_generation
        or summary.get("evidence_revision") != model.evidence_revision
    ):
        return "test_superseded"
    if provider.last_liveness_status != "ok":
        return "provider_test_required"
    if provider.last_liveness_summary.get(
        "configuration_fingerprint"
    ) != provider_configuration_fingerprint(provider, credential_revision):
        return "provider_test_required"
    if not summary.get("required_checks_passed"):
        return "capability_test_required"
    return None
